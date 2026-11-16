"""Scheduling and assignment APIs for Milestone 6 Phase 2."""

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_employee
from app.db.session import get_db
from app.models.applications import Application
from app.models.auth import Employee, EmployeeRole
from app.models.interviews import InterviewFeedback, InterviewRound, InterviewerAssignment
from app.models.jobs import Job
from app.schemas.interviews import (
    InterviewFeedbackCreate,
    InterviewFeedbackPatch,
    InterviewFeedbackRead,
    InterviewRoundCreate,
    InterviewRoundPatch,
    InterviewRoundPage,
    InterviewRoundResponse,
)
from app.services.interview_feedback import edit_feedback, get_own_feedback, submit_feedback
from app.services.interview_management import (
    add_interviewer,
    authorize_application_read,
    authorize_round_read,
    close_round,
    create_round,
    interview_error,
    remove_interviewer,
    reschedule_round,
)

router = APIRouter(tags=["interviews"])


def _serialize_round(db: Session, round_: InterviewRound, actor: Employee) -> InterviewRoundResponse:
    application = db.get(Application, round_.application_id)
    candidate = application.candidate
    job = db.get(Job, application.job_id)
    assignment_rows = db.execute(
        select(InterviewerAssignment, Employee).join(
            Employee, Employee.id == InterviewerAssignment.interviewer_employee_id
        ).where(InterviewerAssignment.interview_round_id == round_.id)
        .order_by(InterviewerAssignment.assigned_at, InterviewerAssignment.id)
    ).all()
    own_feedback = db.scalar(select(InterviewFeedback.id).where(
        InterviewFeedback.interview_round_id == round_.id,
        InterviewFeedback.interviewer_employee_id == actor.id,
    )) is not None
    feedback_rows = db.execute(
        select(InterviewFeedback, Employee).join(
            Employee, Employee.id == InterviewFeedback.interviewer_employee_id
        ).where(InterviewFeedback.interview_round_id == round_.id)
        .order_by(InterviewFeedback.created_at, InterviewFeedback.id)
    ).all()
    can_see_all = actor.role != EmployeeRole.INTERVIEWER or own_feedback
    visible_feedback = [
        {**feedback.__dict__, "interviewer_name": employee.full_name}
        for feedback, employee in feedback_rows
        if can_see_all or feedback.interviewer_employee_id == actor.id
    ]
    interviewers = [
        {
            "id": assignment.id,
            "interviewer_employee_id": assignment.interviewer_employee_id,
            "interviewer_name": employee.full_name,
            "assigned_at": assignment.assigned_at,
            "removed_by_id": assignment.removed_by_id,
            "removed_at": assignment.removed_at,
        }
        for assignment, employee in assignment_rows
        if actor.role != EmployeeRole.INTERVIEWER or assignment.interviewer_employee_id == actor.id
    ]
    return InterviewRoundResponse(
        id=round_.id,
        application_id=None if actor.role == EmployeeRole.INTERVIEWER else round_.application_id,
        candidate_full_name=candidate.full_name,
        job_title=job.title,
        starts_at=round_.starts_at,
        ends_at=round_.ends_at,
        timezone_name=round_.timezone_name,
        status=round_.status,
        created_at=round_.created_at,
        updated_at=round_.updated_at,
        interviewers=interviewers,
        feedback=visible_feedback,
    )


def _get_round(db: Session, round_id: uuid.UUID) -> InterviewRound:
    round_ = db.get(InterviewRound, round_id)
    if round_ is None:
        raise interview_error(404, "not_found", "Interview round not found")
    return round_


def _serialize_feedback(db: Session, feedback: InterviewFeedback) -> InterviewFeedbackRead:
    employee = db.get(Employee, feedback.interviewer_employee_id)
    return InterviewFeedbackRead(
        id=feedback.id,
        interviewer_employee_id=feedback.interviewer_employee_id,
        interviewer_name=employee.full_name if employee else None,
        rating=feedback.rating,
        strengths=feedback.strengths,
        areas_for_improvement=feedback.areas_for_improvement,
        recommendation=feedback.recommendation,
        created_at=feedback.created_at,
        updated_at=feedback.updated_at,
    )


@router.post("/interview-assignments/{assignment_id}/feedback", response_model=InterviewFeedbackRead,
             status_code=status.HTTP_201_CREATED)
def create_interview_feedback(assignment_id: uuid.UUID, data: InterviewFeedbackCreate,
                              actor: Employee = Depends(get_current_employee), db: Session = Depends(get_db)):
    feedback = submit_feedback(db, assignment_id, actor, data.model_dump())
    return _serialize_feedback(db, feedback)


@router.get("/interview-assignments/{assignment_id}/feedback", response_model=InterviewFeedbackRead)
def read_own_interview_feedback(assignment_id: uuid.UUID,
                                actor: Employee = Depends(get_current_employee), db: Session = Depends(get_db)):
    return _serialize_feedback(db, get_own_feedback(db, assignment_id, actor))


@router.patch("/interview-assignments/{assignment_id}/feedback", response_model=InterviewFeedbackRead)
def patch_own_interview_feedback(assignment_id: uuid.UUID, data: InterviewFeedbackPatch,
                                 actor: Employee = Depends(get_current_employee), db: Session = Depends(get_db)):
    feedback = edit_feedback(db, assignment_id, actor, data.model_dump(exclude_unset=True))
    return _serialize_feedback(db, feedback)


@router.post("/applications/{application_id}/interview-rounds", response_model=InterviewRoundResponse,
             response_model_exclude_none=True,
             status_code=status.HTTP_201_CREATED)
def create_interview_round(application_id: uuid.UUID, data: InterviewRoundCreate,
                           actor: Employee = Depends(get_current_employee), db: Session = Depends(get_db)):
    if actor.role not in (EmployeeRole.HR_MANAGER, EmployeeRole.RECRUITER):
        raise interview_error(403, "forbidden", "Only HR Managers and assigned Recruiters may schedule interviews")
    round_ = create_round(db, application_id=application_id, starts_at=data.starts_at, ends_at=data.ends_at,
                          timezone_name=data.timezone_name, interviewer_ids=data.interviewer_ids, actor=actor)
    authorize_round_read(db, actor, round_)
    return _serialize_round(db, round_, actor)


@router.get("/applications/{application_id}/interview-rounds", response_model=InterviewRoundPage,
            response_model_exclude_none=True)
def list_application_interview_rounds(application_id: uuid.UUID, page: int = Query(1, ge=1),
                                      page_size: int = Query(20, ge=1, le=100),
                                      actor: Employee = Depends(get_current_employee), db: Session = Depends(get_db)):
    if actor.role == EmployeeRole.INTERVIEWER:
        raise interview_error(403, "dedicated_endpoint_required",
                              "Interviewers must use the dedicated assigned-interview endpoints")
    application = db.get(Application, application_id)
    if application is None:
        raise interview_error(404, "not_found", "Application not found")
    authorize_application_read(db, actor, application)
    base = select(InterviewRound).where(InterviewRound.application_id == application_id)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rounds = db.scalars(base.order_by(InterviewRound.created_at.desc(), InterviewRound.id)
                        .offset((page - 1) * page_size).limit(page_size)).all()
    return InterviewRoundPage(items=[_serialize_round(db, r, actor) for r in rounds], page=page,
                              page_size=page_size, total=total)


@router.get("/interview-rounds/{round_id}", response_model=InterviewRoundResponse, response_model_exclude_none=True)
def get_interview_round(round_id: uuid.UUID, actor: Employee = Depends(get_current_employee),
                        db: Session = Depends(get_db)):
    round_ = _get_round(db, round_id)
    authorize_round_read(db, actor, round_)
    return _serialize_round(db, round_, actor)


@router.get("/interviews/assigned", response_model=InterviewRoundPage, response_model_exclude_none=True)
def list_assigned_interviews(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                             actor: Employee = Depends(get_current_employee), db: Session = Depends(get_db)):
    if actor.role != EmployeeRole.INTERVIEWER:
        raise interview_error(403, "forbidden", "This endpoint is for Interviewers")
    base = select(InterviewRound).join(
        InterviewerAssignment, InterviewerAssignment.interview_round_id == InterviewRound.id
    ).where(InterviewerAssignment.interviewer_employee_id == actor.id,
            InterviewerAssignment.removed_at.is_(None))
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rounds = db.scalars(base.order_by(InterviewRound.starts_at, InterviewRound.id)
                        .offset((page - 1) * page_size).limit(page_size)).all()
    return InterviewRoundPage(items=[_serialize_round(db, r, actor) for r in rounds], page=page,
                              page_size=page_size, total=total)


@router.patch("/interview-rounds/{round_id}", response_model=InterviewRoundResponse, response_model_exclude_none=True)
def patch_interview_round(round_id: uuid.UUID, data: InterviewRoundPatch,
                          actor: Employee = Depends(get_current_employee), db: Session = Depends(get_db)):
    if data.status is not None:
        round_ = close_round(db, _get_round(db, round_id), target=data.status, actor=actor)
    else:
        round_ = reschedule_round(db, _get_round(db, round_id), starts_at=data.starts_at, ends_at=data.ends_at,
                                  timezone_name=data.timezone_name, actor=actor)
    return _serialize_round(db, round_, actor)


@router.post("/interview-rounds/{round_id}/cancel", response_model=InterviewRoundResponse, response_model_exclude_none=True)
def cancel_interview_round(round_id: uuid.UUID, actor: Employee = Depends(get_current_employee),
                           db: Session = Depends(get_db)):
    round_ = close_round(db, _get_round(db, round_id), target="cancelled", actor=actor)
    return _serialize_round(db, round_, actor)


@router.post("/interview-rounds/{round_id}/complete", response_model=InterviewRoundResponse, response_model_exclude_none=True)
def complete_interview_round(round_id: uuid.UUID, actor: Employee = Depends(get_current_employee),
                             db: Session = Depends(get_db)):
    round_ = close_round(db, _get_round(db, round_id), target="completed", actor=actor)
    return _serialize_round(db, round_, actor)


@router.put("/interview-rounds/{round_id}/interviewers/{employee_id}", response_model=dict,
            status_code=status.HTTP_201_CREATED)
def assign_interviewer(round_id: uuid.UUID, employee_id: uuid.UUID,
                       actor: Employee = Depends(get_current_employee), db: Session = Depends(get_db)):
    assignment = add_interviewer(db, _get_round(db, round_id), employee_id, actor)
    return {"id": assignment.id, "interviewer_employee_id": assignment.interviewer_employee_id,
            "assigned_by_id": assignment.assigned_by_id, "assigned_at": assignment.assigned_at}


@router.delete("/interview-rounds/{round_id}/interviewers/{employee_id}", status_code=status.HTTP_204_NO_CONTENT)
def unassign_interviewer(round_id: uuid.UUID, employee_id: uuid.UUID,
                         actor: Employee = Depends(get_current_employee), db: Session = Depends(get_db)):
    remove_interviewer(db, _get_round(db, round_id), employee_id, actor)
    return None
