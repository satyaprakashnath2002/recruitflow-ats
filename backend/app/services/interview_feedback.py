"""Interview feedback operations with revision and audit writes in one transaction."""

import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import utcnow
from app.models.auth import AuditLog, Employee, EmployeeRole, EmployeeStatus
from app.models.interviews import (
    InterviewFeedback,
    InterviewFeedbackRevision,
    InterviewRound,
    InterviewerAssignment,
)
from app.services.interview_management import interview_error


_FIELDS = ("rating", "strengths", "areas_for_improvement", "recommendation")


def _owned_assignment(db: Session, assignment_id: uuid.UUID, actor: Employee, *, lock: bool = False):
    if actor.role != EmployeeRole.INTERVIEWER:
        raise interview_error(403, "forbidden", "Only Interviewers may manage their own feedback")
    if actor.status != EmployeeStatus.ACTIVE:
        raise interview_error(403, "forbidden", "Inactive Interviewers cannot manage feedback")
    statement = select(InterviewerAssignment).where(
        InterviewerAssignment.id == assignment_id,
        InterviewerAssignment.interviewer_employee_id == actor.id,
    )
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    assignment = db.scalar(statement)
    if assignment is None:
        raise interview_error(404, "not_found", "Feedback not found")
    return assignment


def _round_for_assignment(db: Session, assignment: InterviewerAssignment, *, lock: bool):
    statement = select(InterviewRound).where(InterviewRound.id == assignment.interview_round_id)
    if lock:
        statement = statement.with_for_update()
    round_ = db.scalar(statement)
    if round_ is None:
        raise interview_error(404, "not_found", "Feedback not found")
    return round_


def _audit(db: Session, *, round_: InterviewRound, actor: Employee, feedback: InterviewFeedback,
           action: str, changed_fields: list[str] | None = None) -> None:
    db.add(AuditLog(
        actor_employee_id=actor.id,
        actor_kind="employee",
        target_application_id=round_.application_id,
        action=action,
        details={
            "actor_employee_id": str(actor.id),
            "application_id": str(round_.application_id),
            "round_id": str(round_.id),
            "assignment_id": str(feedback.assignment.id),
            "feedback_id": str(feedback.id),
            "changed_fields": changed_fields or list(_FIELDS),
            "occurred_at": utcnow().isoformat(),
        },
        created_at=utcnow(),
    ))


def _current_feedback(db: Session, assignment: InterviewerAssignment, *, lock: bool = False):
    statement = select(InterviewFeedback).where(
        InterviewFeedback.interview_round_id == assignment.interview_round_id,
        InterviewFeedback.interviewer_employee_id == assignment.interviewer_employee_id,
    )
    if lock:
        statement = statement.with_for_update()
    return db.scalar(statement)


def submit_feedback(db: Session, assignment_id: uuid.UUID, actor: Employee, values: dict) -> InterviewFeedback:
    try:
        # Lock order is round -> assignment -> feedback, matching round completion.
        initial = _owned_assignment(db, assignment_id, actor)
        round_ = _round_for_assignment(db, initial, lock=True)
        assignment = _owned_assignment(db, assignment_id, actor, lock=True)
        if assignment.removed_at is not None:
            raise interview_error(409, "assignment_removed", "Removed assignments cannot submit feedback")
        if round_.status != "scheduled":
            raise interview_error(409, "round_closed", "Feedback can only be submitted for Scheduled interviews")
        if _current_feedback(db, assignment, lock=True) is not None:
            raise interview_error(409, "feedback_exists", "Feedback has already been submitted for this assignment")
        feedback = InterviewFeedback(
            interview_round_id=round_.id,
            interviewer_employee_id=actor.id,
            **values,
        )
        db.add(feedback)
        db.flush()
        # Relationship may not be loaded via composite-FK assignment; identify it explicitly.
        feedback.assignment = assignment
        _audit(db, round_=round_, actor=actor, feedback=feedback, action="interview.feedback_created")
        db.commit()
        db.refresh(feedback)
        return feedback
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        message = str(exc.orig).lower()
        if constraint_name == "uq_interview_feedback_round_employee" or (
            "unique constraint failed" in message and "interview_feedback" in message
        ):
            raise interview_error(409, "feedback_exists", "Feedback has already been submitted for this assignment") from exc
        raise
    except Exception:
        db.rollback()
        raise


def get_own_feedback(db: Session, assignment_id: uuid.UUID, actor: Employee) -> InterviewFeedback:
    assignment = _owned_assignment(db, assignment_id, actor)
    if assignment.removed_at is not None:
        raise interview_error(404, "not_found", "Feedback not found")
    feedback = _current_feedback(db, assignment)
    if feedback is None:
        raise interview_error(404, "not_found", "Feedback not found")
    return feedback


def edit_feedback(db: Session, assignment_id: uuid.UUID, actor: Employee, changes: dict) -> InterviewFeedback:
    try:
        initial = _owned_assignment(db, assignment_id, actor)
        round_ = _round_for_assignment(db, initial, lock=True)
        assignment = _owned_assignment(db, assignment_id, actor, lock=True)
        if assignment.removed_at is not None:
            raise interview_error(409, "assignment_removed", "Removed assignments cannot modify feedback")
        feedback = _current_feedback(db, assignment, lock=True)
        if feedback is None:
            raise interview_error(404, "not_found", "Feedback not found")
        if round_.status != "scheduled":
            raise interview_error(409, "round_closed", "Feedback cannot be modified after interview closure")
        prior = {name: getattr(feedback, name) for name in _FIELDS}
        resulting = {**prior, **changes}
        # Enforce complete-record invariants even for partial PATCH payloads.
        if not (1 <= resulting["rating"] <= 5) or any(
            not isinstance(resulting[field], str) or not resulting[field].strip()
            or len(resulting[field]) > 5000 for field in ("strengths", "areas_for_improvement")
        ) or resulting["recommendation"] not in ("strong_hire", "hire", "no_hire", "strong_no_hire"):
            raise interview_error(422, "validation_error", "The resulting feedback record is invalid")
        db.add(InterviewFeedbackRevision(
            feedback_id=feedback.id,
            **prior,
            changed_by_employee_id=actor.id,
            created_at=utcnow(),
        ))
        for name, value in changes.items():
            setattr(feedback, name, value)
        feedback.updated_at = utcnow()
        db.flush()
        feedback.assignment = assignment
        _audit(db, round_=round_, actor=actor, feedback=feedback,
               action="interview.feedback_modified", changed_fields=sorted(changes))
        db.commit()
        db.refresh(feedback)
        return feedback
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
