"""Opt-in integration test for PostgreSQL's concurrent application uniqueness guard.

Run only against a separately created `recruitflow_m5_test` database after
`alembic upgrade head`; this test refuses every other database name.
"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.engine import make_url

from app.models.applications import Application, Candidate
from app.models.auth import Employee, EmployeeRole
from app.models.jobs import Job


@pytest.mark.skipif(not __import__("os").environ.get("MILESTONE5_TEST_DATABASE_URL"), reason="requires explicit disposable PostgreSQL URL")
def test_postgres_rejects_concurrent_duplicate_candidate_job_applications() -> None:
    import os

    url = os.environ["MILESTONE5_TEST_DATABASE_URL"]
    parsed = make_url(url)
    if parsed.get_backend_name() != "postgresql" or parsed.database != "recruitflow_m5_test":
        pytest.fail("MILESTONE5_TEST_DATABASE_URL must point to the disposable recruitflow_m5_test PostgreSQL database")

    engine = create_engine(url, pool_size=2, max_overflow=0)
    if not inspect(engine).has_table("applications"):
        pytest.fail("Run `alembic upgrade head` against the disposable test database first")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    suffix = uuid4().hex
    employee_id, candidate_id, job_id = uuid4(), uuid4(), uuid4()
    try:
        with factory.begin() as db:
            db.add(Employee(id=employee_id, email=f"m5-{suffix}@example.test", role=EmployeeRole.ADMIN))
            db.flush()
            db.add(Candidate(id=candidate_id, full_name="Concurrency Test", email=f"c-{suffix}@example.test"))
            db.add(Job(
                id=job_id,
                title="Concurrency Test",
                department="Engineering",
                employment_type="Full-time",
                location="Remote",
                work_mode="Remote",
                description="Disposable integration test row",
                vacancies=1,
                status="published",
                created_by_id=employee_id,
                updated_by_id=employee_id,
            ))

        barrier = Barrier(2)

        def submit() -> str:
            with factory() as db:
                db.add(Application(candidate_id=candidate_id, job_id=job_id, source="public_portal", stage="applied"))
                barrier.wait(timeout=10)
                try:
                    db.commit()
                    return "created"
                except IntegrityError:
                    db.rollback()
                    return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: submit(), range(2)))
        assert sorted(results) == ["conflict", "created"]
        with factory() as db:
            count = db.scalar(select(Application).where(Application.candidate_id == candidate_id, Application.job_id == job_id))
            assert count is not None
            assert len(db.scalars(select(Application).where(Application.candidate_id == candidate_id, Application.job_id == job_id)).all()) == 1
    finally:
        with factory.begin() as db:
            db.query(Application).filter_by(candidate_id=candidate_id, job_id=job_id).delete()
            db.query(Job).filter_by(id=job_id).delete()
            db.query(Candidate).filter_by(id=candidate_id).delete()
            db.query(Employee).filter_by(id=employee_id).delete()
        engine.dispose()
