import asyncio
from datetime import datetime, timedelta, timezone
import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import pytest

from app.core.security import hash_password
from app.models.applications import Application, Candidate
from app.models.auth import Employee, EmployeeRole, EmployeeStatus
from app.models.interviews import InterviewRound, InterviewerAssignment
from app.models.jobs import Job
from app.models.notifications import Notification, ScheduledReminder
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.services.notifications import process_due_reminders
from app.services.interview_management import close_round, create_round, remove_interviewer, reschedule_round

PASSWORD = "Notification test password 77!"


@pytest.fixture
def isolated_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def make_employee(factory, role: EmployeeRole, email: str) -> Employee:
    with factory() as db:
        employee = Employee(
            full_name=role.value.replace("_", " ").title(), email=email, role=role,
            status=EmployeeStatus.ACTIVE, password_hash=hash_password(PASSWORD),
        )
        db.add(employee)
        db.commit()
        db.refresh(employee)
        return employee


def make_round(factory, hr: Employee, interviewer: Employee, starts_at: datetime) -> InterviewRound:
    with factory() as db:
        candidate = Candidate(full_name="Notification Candidate", email=f"{uuid.uuid4()}@example.test")
        job = Job(
            title="Engineer", department="Engineering", employment_type="Full-time", location="Remote",
            work_mode="Remote", description="Test job", vacancies=1, status="published",
            created_by_id=hr.id, updated_by_id=hr.id,
        )
        db.add_all([candidate, job])
        db.flush()
        application = Application(candidate_id=candidate.id, job_id=job.id, source="referral", stage="interview")
        db.add(application)
        db.flush()
        round_ = create_round(
            db, application_id=application.id, starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=1), timezone_name="Asia/Kolkata",
            interviewer_ids=[interviewer.id], actor=hr,
        )
        return round_


def test_notification_center_scopes_filter_and_read_state(isolated_factory) -> None:
    # Starlette 1.10's TestClient depends on httpx2; import it only for this
    # API test so the database-only service tests can still run when the local
    # environment has not installed the declared development extra.
    import httpx2

    factory = isolated_factory
    recipient = make_employee(factory, EmployeeRole.INTERVIEWER, "notify-interviewer@example.com")
    other = make_employee(factory, EmployeeRole.INTERVIEWER, "notify-other@example.com")
    hr = make_employee(factory, EmployeeRole.HR_MANAGER, "notify-hr@example.com")
    start = datetime.now(timezone.utc) + timedelta(days=3)
    make_round(factory, hr, recipient, start)
    def database_dependency():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = database_dependency

    async def exercise_api() -> None:
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            async def login(employee: Employee) -> dict[str, str]:
                result = await client.post("/api/v1/auth/login", json={"email": employee.email, "password": PASSWORD})
                assert result.status_code == 200, result.text
                return {"Authorization": f"Bearer {result.json()['access_token']}"}

            own_headers = await login(recipient)
            other_headers = await login(other)
            response = await client.get("/api/v1/notifications?unread=true&page=1&page_size=10", headers=own_headers)
            assert response.status_code == 200, response.text
            assert response.json()["total"] == 1
            item = response.json()["items"][0]
            assert item["event_type"] == "interview_scheduled"
            assert "Candidate" not in item["body"]
            assert (await client.get("/api/v1/notifications", headers=other_headers)).json()["total"] == 0
            marked = await client.patch(f"/api/v1/notifications/{item['id']}", headers=own_headers)
            assert marked.status_code == 200
            assert marked.json()["read_at"] is not None
            assert (await client.get("/api/v1/notifications?unread=true", headers=own_headers)).json()["total"] == 0

    try:
        asyncio.run(exercise_api())
    finally:
        app.dependency_overrides.clear()


def test_due_reminder_delivery_is_idempotent_and_uses_current_round(isolated_factory) -> None:
    factory = isolated_factory
    hr = make_employee(factory, EmployeeRole.HR_MANAGER, "reminder-hr@example.test")
    interviewer = make_employee(factory, EmployeeRole.INTERVIEWER, "reminder-interviewer@example.test")
    starts = datetime.now(timezone.utc) + timedelta(hours=25)
    round_ = make_round(factory, hr, interviewer, starts)
    with factory() as db:
        reminders = db.scalars(select(ScheduledReminder).where(
            ScheduledReminder.interview_round_id == round_.id
        )).all()
        assert {r.offset_minutes for r in reminders} == {60, 1440}
        due = next(r for r in reminders if r.offset_minutes == 1440)
        processed_at = due.scheduled_at.replace(tzinfo=timezone.utc) + timedelta(seconds=1)
        assert process_due_reminders(db, now=processed_at) == 1
        assert process_due_reminders(db, now=processed_at + timedelta(seconds=1)) == 0

    with factory() as db:
        delivered = db.scalars(select(Notification).where(
            Notification.recipient_employee_id == interviewer.id,
            Notification.event_type == "interview_reminder_24h",
        )).all()
        assert len(delivered) == 1
        due = db.scalar(select(ScheduledReminder).where(
            ScheduledReminder.interview_round_id == round_.id,
            ScheduledReminder.offset_minutes == 1440,
        ))
        assert due.state == "delivered"
        assert due.attempt_count == 1


def test_removed_assignment_supersedes_its_pending_reminders(isolated_factory) -> None:
    factory = isolated_factory
    hr = make_employee(factory, EmployeeRole.HR_MANAGER, "remove-hr@example.test")
    interviewer = make_employee(factory, EmployeeRole.INTERVIEWER, "remove-interviewer@example.test")
    round_ = make_round(factory, hr, interviewer, datetime.now(timezone.utc) + timedelta(days=3))
    with factory() as db:
        assignment = db.scalar(select(InterviewerAssignment).where(
            InterviewerAssignment.interview_round_id == round_.id
        ))
        remove_interviewer(db, round_, interviewer.id, actor=hr)
        states = db.scalars(select(ScheduledReminder.state).where(
            ScheduledReminder.interview_round_id == round_.id
        )).all()
        assert assignment is not None
        assert assignment.removed_at is not None
        assert states and set(states) == {"superseded"}


def test_reschedule_and_cancellation_supersede_obsolete_reminders(isolated_factory) -> None:
    factory = isolated_factory
    hr = make_employee(factory, EmployeeRole.HR_MANAGER, "reschedule-hr@example.test")
    interviewer = make_employee(factory, EmployeeRole.INTERVIEWER, "reschedule-interviewer@example.test")
    round_ = make_round(factory, hr, interviewer, datetime.now(timezone.utc) + timedelta(days=3))
    with factory() as db:
        next_start = datetime.now(timezone.utc) + timedelta(days=5)
        reschedule_round(db, round_, starts_at=next_start, ends_at=next_start + timedelta(hours=1),
                         timezone_name="Asia/Kolkata", actor=hr)
        states = db.scalars(select(ScheduledReminder.state).where(
            ScheduledReminder.interview_round_id == round_.id
        )).all()
        assert states.count("superseded") == 2
        assert states.count("pending") == 2
        cancelled = db.get(InterviewRound, round_.id)
        close_round(db, cancelled, target="cancelled", actor=hr)
        states = db.scalars(select(ScheduledReminder.state).where(
            ScheduledReminder.interview_round_id == round_.id
        )).all()
        assert set(states) == {"superseded"}
        event_types = db.scalars(select(Notification.event_type).where(
            Notification.resource_id == round_.id
        )).all()
        assert "interview_scheduled" in event_types
        assert "interview_rescheduled" in event_types
        assert "interview_cancelled" in event_types
