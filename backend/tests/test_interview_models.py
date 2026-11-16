from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.applications import Application, Candidate
from app.models.auth import Employee, EmployeeRole, EmployeeStatus
from app.models.interviews import (
    InterviewFeedback,
    InterviewFeedbackRevision,
    InterviewRecommendation,
    InterviewRound,
    InterviewRoundStatus,
    InterviewerAssignment,
)
from app.models.jobs import Job, JobStatus


@pytest.fixture
def db_session() -> Session:
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    Base.metadata.drop_all(engine)
    engine.dispose()


def create_application(session: Session) -> Application:
    admin = Employee(
        full_name="M6 Admin",
        email="m6-admin@example.test",
        role=EmployeeRole.ADMIN,
        status=EmployeeStatus.ACTIVE,
        password_hash="test-hash",
    )
    candidate = Candidate(full_name="M6 Candidate", email="m6-candidate@example.test")
    job = Job(
        title="Engineer",
        department="Engineering",
        employment_type="Full-time",
        location="Bengaluru",
        work_mode="Hybrid",
        description="Build software.",
        vacancies=1,
        status=JobStatus.PUBLISHED.value,
        created_by=admin,
        updated_by=admin,
    )
    application = Application(candidate=candidate, job=job, source="referral", stage="interview")
    session.add(application)
    session.flush()
    return application


def new_round(application: Application, **changes) -> InterviewRound:
    starts_at = datetime.now(timezone.utc) + timedelta(days=2)
    values = dict(
        application_id=application.id,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        timezone_name="Asia/Kolkata",
    )
    values.update(changes)
    return InterviewRound(**values)


def create_interviewer(session: Session) -> Employee:
    employee = Employee(
        full_name="M6 Interviewer",
        email="m6-interviewer@example.test",
        role=EmployeeRole.INTERVIEWER,
        status=EmployeeStatus.ACTIVE,
        password_hash="test-hash",
    )
    session.add(employee)
    session.flush()
    return employee


def test_interview_relationships_and_utc_normalization(db_session: Session) -> None:
    application = create_application(db_session)
    interviewer = create_interviewer(db_session)
    local_zone = timezone(timedelta(hours=5, minutes=30))
    start = datetime(2030, 1, 1, 10, 0, tzinfo=local_zone)
    round_ = InterviewRound(
        application=application,
        starts_at=start,
        ends_at=start + timedelta(hours=1),
        timezone_name="Asia/Kolkata",
    )
    assignment = InterviewerAssignment(
        interview_round=round_, interviewer=interviewer, assigned_by=interviewer
    )
    db_session.add(round_)
    db_session.add(assignment)
    db_session.flush()
    feedback = InterviewFeedback(
        interview_round_id=round_.id,
        assignment=assignment,
        interviewer_employee_id=interviewer.id,
        rating=4,
        strengths="Clear communication",
        areas_for_improvement="More detail on testing",
        recommendation=InterviewRecommendation.HIRE.value,
    )
    db_session.add(feedback)
    db_session.flush()

    assert round_.application is application
    assert application.interview_rounds == [round_]
    assert round_.assignments == [assignment]
    assert assignment.interviewer is interviewer
    assert assignment.feedback is feedback
    assert round_.starts_at.utcoffset() == timedelta(0)
    assert round_.timezone_name == "Asia/Kolkata"


def test_assignment_must_be_unique_per_round_and_interviewer(db_session: Session) -> None:
    round_ = new_round(create_application(db_session))
    interviewer = create_interviewer(db_session)
    db_session.add(round_)
    db_session.flush()
    db_session.add(
        InterviewerAssignment(interview_round=round_, interviewer=interviewer, assigned_by=interviewer)
    )
    db_session.commit()
    db_session.add(
        InterviewerAssignment(interview_round=round_, interviewer=interviewer, assigned_by=interviewer)
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_feedback_must_be_unique_per_round_interviewer_and_assigned(db_session: Session) -> None:
    round_ = new_round(create_application(db_session))
    interviewer = create_interviewer(db_session)
    other_interviewer = Employee(
        full_name="Other Interviewer",
        email="m6-other-interviewer@example.test",
        role=EmployeeRole.INTERVIEWER,
        status=EmployeeStatus.ACTIVE,
        password_hash="test-hash",
    )
    db_session.add(other_interviewer)
    db_session.add(round_)
    db_session.flush()
    assignment = InterviewerAssignment(
        interview_round=round_, interviewer=interviewer, assigned_by=interviewer
    )
    db_session.add(assignment)
    db_session.flush()
    feedback = InterviewFeedback(
        interview_round_id=round_.id,
        assignment=assignment,
        interviewer_employee_id=interviewer.id,
        rating=4,
        strengths="Clear communication",
        areas_for_improvement="More detail on testing",
        recommendation="hire",
    )
    db_session.add(feedback)
    db_session.commit()

    unassigned_feedback = InterviewFeedback(
        interview_round_id=round_.id,
        interviewer_employee_id=other_interviewer.id,
        rating=3,
        strengths="Not assigned",
        areas_for_improvement="Not assigned",
        recommendation="hire",
    )
    db_session.add(unassigned_feedback)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    duplicate = InterviewFeedback(
        interview_round_id=round_.id,
        interviewer_employee_id=interviewer.id,
        rating=3,
        strengths="Different strengths",
        areas_for_improvement="Different areas",
        recommendation="no_hire",
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


@pytest.mark.parametrize(
    ("rating", "recommendation"),
    [(0, "hire"), (6, "hire"), (3, "maybe")],
)
def test_feedback_rating_and_recommendation_constraints(
    db_session: Session, rating: int, recommendation: str
) -> None:
    round_ = new_round(create_application(db_session))
    interviewer = create_interviewer(db_session)
    db_session.add(round_)
    db_session.flush()
    assignment = InterviewerAssignment(
        interview_round=round_, interviewer=interviewer, assigned_by=interviewer
    )
    db_session.add(assignment)
    db_session.flush()
    db_session.add(
        InterviewFeedback(
            interview_round_id=round_.id,
            assignment=assignment,
            interviewer_employee_id=interviewer.id,
            rating=rating,
            strengths="Clear communication",
            areas_for_improvement="More detail on testing",
            recommendation=recommendation,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_interview_time_validation_and_lifecycle_values(db_session: Session) -> None:
    application = create_application(db_session)
    starts_at = datetime.now(timezone.utc) + timedelta(days=2)
    invalid_order = InterviewRound(
        application_id=application.id,
        starts_at=starts_at,
        ends_at=starts_at,
        timezone_name="UTC",
    )
    db_session.add(invalid_order)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    with pytest.raises(ValueError, match="timezone-aware"):
        new_round(application, starts_at=datetime(2030, 1, 1, 10, 0))
    with pytest.raises(ValueError, match="IANA timezone"):
        new_round(application, timezone_name="Not/A_Real_Zone")

    db_session.expire_all()
    application = create_application(db_session)
    round_ = new_round(application)
    round_.status = "invalid"
    db_session.add(round_)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_foreign_key_integrity_and_feedback_revision_append_only(db_session: Session) -> None:
    db_session.add(
        InterviewRound(
            application_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            starts_at=datetime.now(timezone.utc),
            ends_at=datetime.now(timezone.utc) + timedelta(hours=1),
            timezone_name="UTC",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    round_ = new_round(create_application(db_session))
    interviewer = create_interviewer(db_session)
    db_session.add(round_)
    db_session.flush()
    assignment = InterviewerAssignment(
        interview_round=round_, interviewer=interviewer, assigned_by=interviewer
    )
    db_session.add(assignment)
    db_session.flush()
    feedback = InterviewFeedback(
        interview_round_id=round_.id,
        assignment=assignment,
        interviewer_employee_id=interviewer.id,
        rating=5,
        strengths="Strong examples",
        areas_for_improvement="None recorded",
        recommendation="strong_hire",
    )
    db_session.add(feedback)
    db_session.flush()
    revision = InterviewFeedbackRevision(
        feedback=feedback,
        rating=4,
        strengths="Prior strengths",
        areas_for_improvement="Prior improvement",
        recommendation="hire",
        changed_by=interviewer,
    )
    db_session.add(revision)
    db_session.commit()
    revision.rating = 3
    with pytest.raises(ValueError, match="append-only"):
        db_session.commit()
    db_session.rollback()
    assert db_session.scalar(select(InterviewFeedbackRevision.rating)) == 4
