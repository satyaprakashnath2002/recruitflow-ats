"""Opt-in direct-SQL protection checks for closed interview rounds.

Uses only the explicitly disposable `recruitflow_m6_test` database.
"""

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app.models.applications import Application, Candidate
from app.models.auth import Employee, EmployeeRole, EmployeeStatus
from app.models.interviews import InterviewRound
from app.models.jobs import Job


@pytest.mark.skipif(not os.environ.get("MILESTONE6_TEST_DATABASE_URL"), reason="requires disposable PostgreSQL database")
def test_postgres_terminal_rounds_reject_direct_schedule_updates() -> None:
    url = os.environ["MILESTONE6_TEST_DATABASE_URL"]
    parsed = make_url(url)
    if parsed.get_backend_name() != "postgresql" or parsed.database != "recruitflow_m6_test":
        pytest.fail("MILESTONE6_TEST_DATABASE_URL must target disposable recruitflow_m6_test")
    engine = create_engine(url, pool_size=2, max_overflow=0)
    if not inspect(engine).has_table("interview_rounds"):
        pytest.fail("Run Alembic upgrade head against the disposable database first")
    factory = sessionmaker(engine)
    suffix = uuid4().hex
    admin_id, candidate_id, job_id, app_id = uuid4(), uuid4(), uuid4(), uuid4()
    round_ids = [uuid4(), uuid4()]
    start = datetime.now(timezone.utc) + timedelta(days=60)
    try:
        with factory.begin() as db:
            db.add_all([
                Employee(id=admin_id, full_name="Terminal Trigger Admin", email=f"trigger-{suffix}@example.test",
                         role=EmployeeRole.ADMIN, status=EmployeeStatus.ACTIVE),
                Candidate(id=candidate_id, full_name="Terminal Trigger Candidate",
                          email=f"trigger-candidate-{suffix}@example.test"),
                Job(id=job_id, title="Terminal Trigger Job", department="Engineering", employment_type="Full-time",
                    location="Remote", work_mode="Remote", description="Trigger test", vacancies=1,
                    status="published", created_by_id=admin_id, updated_by_id=admin_id),
            ])
            db.add(Application(id=app_id, candidate_id=candidate_id, job_id=job_id,
                               source="referral", stage="interview"))
            db.add_all([
                InterviewRound(id=round_ids[0], application_id=app_id, starts_at=start,
                               ends_at=start + timedelta(hours=1), timezone_name="Asia/Kolkata", status="completed"),
                InterviewRound(id=round_ids[1], application_id=app_id, starts_at=start + timedelta(days=1),
                               ends_at=start + timedelta(days=1, hours=1), timezone_name="Asia/Kolkata", status="cancelled"),
            ])

        updates = (
            "UPDATE interview_rounds SET starts_at = starts_at + interval '1 minute' WHERE id = :id",
            "UPDATE interview_rounds SET ends_at = ends_at + interval '1 minute' WHERE id = :id",
            "UPDATE interview_rounds SET timezone_name = 'UTC' WHERE id = :id",
            "UPDATE interview_rounds SET status = 'scheduled' WHERE id = :id",
        )
        for round_id in round_ids:
            for statement in updates:
                with factory() as db:
                    with pytest.raises(DBAPIError, match="terminal interview rounds are immutable"):
                        db.execute(text(statement), {"id": str(round_id)})
                    db.rollback()
        with factory() as db:
            with pytest.raises(DBAPIError, match="interview history is retained"):
                db.execute(text("DELETE FROM interview_rounds WHERE id = :id"), {"id": str(round_ids[0])})
                db.commit()
            db.rollback()
    finally:
        engine.dispose()
