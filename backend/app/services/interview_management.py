"""Interview scheduling operations and their transactional audit records."""

import uuid
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import utcnow
from app.models.applications import Application
from app.models.auth import AuditLog, Employee, EmployeeRole, EmployeeStatus
from app.models.interviews import InterviewRound, InterviewerAssignment
from app.models.jobs import JobAssignment
from app.services.notifications import (
    create_round_event,
    schedule_round_reminders,
    supersede_interviewer_reminders,
)


def interview_error(code: int, status_text: str, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail={"status": status_text, "message": message})


def _audit(db: Session, *, application_id, action: str, actor: Employee, details: dict) -> None:
    db.add(AuditLog(
        actor_employee_id=actor.id,
        actor_kind="employee",
        target_application_id=application_id,
        action=action,
        details=details,
        created_at=utcnow(),
    ))


def _assigned_job(db: Session, employee: Employee, job_id) -> bool:
    return db.scalar(select(JobAssignment.job_id).where(
        JobAssignment.employee_id == employee.id, JobAssignment.job_id == job_id
    )) is not None


def authorize_application_read(db: Session, actor: Employee, application: Application) -> None:
    if actor.role in (EmployeeRole.ADMIN, EmployeeRole.HR_MANAGER):
        return
    if actor.role == EmployeeRole.RECRUITER and _assigned_job(db, actor, application.job_id):
        return
    if actor.role == EmployeeRole.INTERVIEWER and db.scalar(select(InterviewerAssignment.id).join(
        InterviewRound, InterviewRound.id == InterviewerAssignment.interview_round_id
    ).where(
        InterviewRound.application_id == application.id,
        InterviewerAssignment.interviewer_employee_id == actor.id,
        InterviewerAssignment.removed_at.is_(None),
    )) is not None:
        return
    raise interview_error(404, "not_found", "Interview round not found")


def authorize_round_read(db: Session, actor: Employee, round_: InterviewRound) -> None:
    if actor.role == EmployeeRole.INTERVIEWER:
        assigned = db.scalar(select(InterviewerAssignment.id).where(
            InterviewerAssignment.interview_round_id == round_.id,
            InterviewerAssignment.interviewer_employee_id == actor.id,
            InterviewerAssignment.removed_at.is_(None),
        ))
        if assigned is not None:
            return
        raise interview_error(404, "not_found", "Interview round not found")
    application = db.get(Application, round_.application_id)
    if application is None:
        raise interview_error(404, "not_found", "Interview round not found")
    authorize_application_read(db, actor, application)


def authorize_round_write(db: Session, actor: Employee, round_: InterviewRound) -> None:
    if actor.role == EmployeeRole.HR_MANAGER:
        return
    app = db.get(Application, round_.application_id)
    if actor.role == EmployeeRole.RECRUITER:
        if app and _assigned_job(db, actor, app.job_id):
            return
        raise interview_error(404, "not_found", "Interview round not found")
    raise interview_error(403, "forbidden", "Insufficient permissions")


def _lock_employees(db: Session, employee_ids: set[uuid.UUID]) -> dict[uuid.UUID, Employee]:
    if not employee_ids:
        return {}
    employees = db.scalars(
        select(Employee).where(Employee.id.in_(employee_ids)).order_by(Employee.id).with_for_update()
    ).all()
    by_id = {employee.id: employee for employee in employees}
    if set(by_id) != employee_ids or any(
        employee.role != EmployeeRole.INTERVIEWER or employee.status != EmployeeStatus.ACTIVE
        for employee in employees
    ):
        raise interview_error(422, "validation_error", "All assigned employees must be active Interviewers")
    return by_id


def _lock_employee_rows(db: Session, employee_ids: set[uuid.UUID]) -> None:
    if not employee_ids:
        return
    rows = db.scalars(select(Employee).where(Employee.id.in_(employee_ids))
                      .order_by(Employee.id).with_for_update()).all()
    if len(rows) != len(employee_ids):
        raise interview_error(404, "not_found", "Interviewer assignment not found")


def _conflicts(db: Session, employee_ids: set[uuid.UUID], starts_at, ends_at, *, exclude_round=None):
    if not employee_ids:
        return set()
    statement = select(InterviewerAssignment.interviewer_employee_id).join(
        InterviewRound, InterviewRound.id == InterviewerAssignment.interview_round_id
    ).where(
        InterviewerAssignment.interviewer_employee_id.in_(employee_ids),
        InterviewerAssignment.removed_at.is_(None),
        InterviewRound.status == "scheduled",
        InterviewRound.starts_at < ends_at,
        InterviewRound.ends_at > starts_at,
    )
    if exclude_round is not None:
        statement = statement.where(InterviewRound.id != exclude_round)
    return set(db.scalars(statement).all())


def _reject_conflicts(db, employee_ids, starts_at, ends_at, *, exclude_round=None):
    if _conflicts(db, employee_ids, starts_at, ends_at, exclude_round=exclude_round):
        raise interview_error(409, "interviewer_schedule_conflict", "One or more interviewers are unavailable for this time")


def create_round(db: Session, *, application_id, starts_at, ends_at, timezone_name, interviewer_ids, actor):
    application = db.scalar(select(Application).where(Application.id == application_id).with_for_update())
    if application is None:
        raise interview_error(404, "not_found", "Application not found")
    if application.stage != "interview":
        raise interview_error(400, "invalid_application_stage", "Application must be in the interview stage")
    authorize_round_write(db, actor, InterviewRound(application_id=application_id))
    ids = set(interviewer_ids)
    _lock_employees(db, ids)
    _reject_conflicts(db, ids, starts_at, ends_at)
    round_ = InterviewRound(
        application_id=application.id, starts_at=starts_at, ends_at=ends_at,
        timezone_name=timezone_name, status="scheduled",
    )
    db.add(round_)
    db.flush()
    _audit(db, application_id=application.id, action="interview.round_created", actor=actor,
           details={"round_id": str(round_.id), "starts_at": starts_at.isoformat(), "ends_at": ends_at.isoformat(), "timezone_name": timezone_name})
    _audit(db, application_id=application.id, action="interview.scheduled", actor=actor,
           details={"round_id": str(round_.id), "starts_at": starts_at.isoformat(), "ends_at": ends_at.isoformat(), "timezone_name": timezone_name})
    for interviewer_id in sorted(ids):
        _add_assignment(db, round_, interviewer_id, actor, audit=True)
    create_round_event(db, round_, "interview_scheduled")
    schedule_round_reminders(db, round_)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return db.get(InterviewRound, round_.id)


def _add_assignment(db, round_, interviewer_id, actor, *, audit):
    assignment = InterviewerAssignment(
        interview_round_id=round_.id,
        interviewer_employee_id=interviewer_id,
        assigned_by_id=actor.id,
    )
    db.add(assignment)
    db.flush()
    if audit:
        _audit(db, application_id=round_.application_id, action="interview.interviewer_assigned", actor=actor,
               details={"round_id": str(round_.id), "assignment_id": str(assignment.id), "interviewer_employee_id": str(interviewer_id)})
    return assignment


def reschedule_round(db: Session, round_, *, starts_at, ends_at, timezone_name, actor):
    round_ = db.scalar(select(InterviewRound).where(InterviewRound.id == round_.id).with_for_update())
    authorize_round_write(db, actor, round_)
    if round_.status != "scheduled":
        raise interview_error(400, "invalid_transition", "Only Scheduled interviews can be rescheduled")
    ids = set(db.scalars(select(InterviewerAssignment.interviewer_employee_id).where(
        InterviewerAssignment.interview_round_id == round_.id, InterviewerAssignment.removed_at.is_(None)
    )).all())
    _lock_employees(db, ids)
    _reject_conflicts(db, ids, starts_at, ends_at, exclude_round=round_.id)
    previous = {"starts_at": round_.starts_at.isoformat(), "ends_at": round_.ends_at.isoformat(), "timezone_name": round_.timezone_name}
    round_.starts_at, round_.ends_at, round_.timezone_name = starts_at, ends_at, timezone_name
    _audit(db, application_id=round_.application_id, action="interview.rescheduled", actor=actor,
           details={"round_id": str(round_.id), "previous": previous, "starts_at": starts_at.isoformat(), "ends_at": ends_at.isoformat(), "timezone_name": timezone_name})
    create_round_event(db, round_, "interview_rescheduled")
    schedule_round_reminders(db, round_)
    db.commit()
    db.refresh(round_)
    return round_


def close_round(db: Session, round_, *, target: str, actor):
    round_ = db.scalar(select(InterviewRound).where(InterviewRound.id == round_.id).with_for_update())
    authorize_round_write(db, actor, round_)
    if round_.status != "scheduled" or target not in ("completed", "cancelled"):
        raise interview_error(400, "invalid_transition", f"Cannot transition interview from {round_.status} to {target}")
    round_.status = target
    _audit(db, application_id=round_.application_id, action=f"interview.{target}", actor=actor,
           details={"round_id": str(round_.id), "from": "scheduled", "to": target})
    create_round_event(db, round_, f"interview_{target}")
    schedule_round_reminders(db, round_)
    db.commit()
    db.refresh(round_)
    return round_


def add_interviewer(db: Session, round_, interviewer_id, actor):
    round_ = db.scalar(select(InterviewRound).where(InterviewRound.id == round_.id).with_for_update())
    authorize_round_write(db, actor, round_)
    if round_.status != "scheduled":
        raise interview_error(400, "assignments_locked", "Interviewer assignments are locked after interview closure")
    current_ids = set(db.scalars(select(InterviewerAssignment.interviewer_employee_id).where(
        InterviewerAssignment.interview_round_id == round_.id, InterviewerAssignment.removed_at.is_(None)
    )).all())
    _lock_employees(db, current_ids | {interviewer_id})
    if interviewer_id in current_ids or db.scalar(select(InterviewerAssignment.id).where(
        InterviewerAssignment.interview_round_id == round_.id,
        InterviewerAssignment.interviewer_employee_id == interviewer_id,
    )) is not None:
        raise interview_error(409, "already_assigned", "This employee already has assignment history for this interview")
    _reject_conflicts(db, {interviewer_id}, round_.starts_at, round_.ends_at, exclude_round=round_.id)
    assignment = _add_assignment(db, round_, interviewer_id, actor, audit=True)
    create_round_event(db, round_, "interview_scheduled", recipients=[interviewer_id])
    schedule_round_reminders(db, round_)
    db.commit()
    db.refresh(assignment)
    return assignment


def remove_interviewer(db: Session, round_, interviewer_id, actor):
    round_ = db.scalar(select(InterviewRound).where(InterviewRound.id == round_.id).with_for_update())
    authorize_round_write(db, actor, round_)
    if round_.status != "scheduled":
        raise interview_error(400, "assignments_locked", "Interviewer assignments are locked after interview closure")
    assignment = db.scalar(select(InterviewerAssignment).where(
        InterviewerAssignment.interview_round_id == round_.id,
        InterviewerAssignment.interviewer_employee_id == interviewer_id,
        InterviewerAssignment.removed_at.is_(None),
    ).with_for_update())
    if assignment is None:
        raise interview_error(404, "not_found", "Interviewer assignment not found")
    _lock_employee_rows(db, {interviewer_id})
    assignment.removed_by_id, assignment.removed_at = actor.id, utcnow()
    _audit(db, application_id=round_.application_id, action="interview.interviewer_removed", actor=actor,
           details={"round_id": str(round_.id), "assignment_id": str(assignment.id), "interviewer_employee_id": str(interviewer_id)})
    supersede_interviewer_reminders(db, round_.id, interviewer_id)
    db.commit()
    return assignment
