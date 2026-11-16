"""Opt-in independent-session concurrency test for final stage decisions.

Requires MILESTONE6_TEST_DATABASE_URL to target the disposable recruitflow_m6_test database.
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import os
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.models.applications import Application, Candidate
from app.models.auth import AuditLog, Employee, EmployeeRole, EmployeeStatus
from app.models.jobs import Job
from app.services.application_management import change_stage


@pytest.mark.skipif(not os.environ.get("MILESTONE6_TEST_DATABASE_URL"), reason="requires disposable PostgreSQL test database")
def test_concurrent_hr_final_stage_decisions_serialize_and_audit_once() -> None:
    url = os.environ["MILESTONE6_TEST_DATABASE_URL"]
    parsed = make_url(url)
    if parsed.get_backend_name() != "postgresql" or parsed.database != "recruitflow_m6_test":
        pytest.fail("MILESTONE6_TEST_DATABASE_URL must point to disposable recruitflow_m6_test")
    engine = create_engine(
        url, pool_size=2, max_overflow=0,
        connect_args={"options": "-c lock_timeout=5s -c statement_timeout=10000"},
    )
    if not inspect(engine).has_table("notifications"):
        pytest.fail("Apply all migrations through 0009 to the disposable M6 test database first")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    suffix = uuid4().hex
    hr_id, candidate_id, job_id, application_id = uuid4(), uuid4(), uuid4(), uuid4()
    with factory.begin() as db:
        db.add(Employee(
            id=hr_id, full_name="Concurrency HR", email=f"stage-hr-{suffix}@example.test",
            role=EmployeeRole.HR_MANAGER, status=EmployeeStatus.ACTIVE,
        ))
        db.add(Job(
            id=job_id, title="Stage Concurrency", department="Engineering", employment_type="Full-time",
            location="Remote", work_mode="Remote", description="Disposable test row", vacancies=1,
            status="published", created_by_id=hr_id, updated_by_id=hr_id,
        ))
        db.add(Candidate(id=candidate_id, full_name="Stage Test", email=f"stage-{suffix}@example.test"))
        db.add(Application(
            id=application_id, candidate_id=candidate_id, job_id=job_id,
            source="referral", stage="applied",
        ))

    barrier = Barrier(2)

    def transition() -> str:
        with factory() as db:
            actor = db.get(Employee, hr_id)
            application = db.get(Application, application_id)
            barrier.wait(timeout=10)
            try:
                change_stage(db, application, target="screening", actor=actor)
                return "changed"
            except HTTPException as exc:
                db.rollback()
                return f"rejected:{exc.status_code}"

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(transition) for _ in range(2)]
            outcomes = [future.result(timeout=15) for future in futures]
        assert sorted(outcomes) == ["changed", "rejected:400"]
        with factory() as db:
            assert db.get(Application, application_id).stage == "screening"
            audit_count = db.scalar(select(func.count(AuditLog.id)).where(
                AuditLog.target_application_id == application_id,
                AuditLog.action == "application.stage_changed",
            ))
            assert audit_count == 1
    except FutureTimeout:
        pytest.fail("Concurrent stage transitions hung instead of completing within the lock timeout")
    finally:
        engine.dispose()

