"""Opt-in independent-transaction PostgreSQL interview overlap test.

Requires an explicitly disposable database named `recruitflow_m6_test`, migrated to head.
Rows remain in that disposable database because interview history is intentionally retained.
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timedelta, timezone
import os
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.models.applications import Application, Candidate
from app.models.auth import AuditLog, Employee, EmployeeRole, EmployeeStatus
from app.models.interviews import InterviewFeedback, InterviewFeedbackRevision, InterviewRound, InterviewerAssignment
from app.models.jobs import Job
from app.services.interview_feedback import edit_feedback, submit_feedback
from app.services.interview_management import add_interviewer, close_round, create_round, reschedule_round
from fastapi import HTTPException


def _seed(url: str, *, assignment_mode: str = "none"):
    parsed = make_url(url)
    if parsed.get_backend_name() != "postgresql" or parsed.database != "recruitflow_m6_test":
        pytest.fail("MILESTONE6_TEST_DATABASE_URL must point to the disposable recruitflow_m6_test database")
    engine = create_engine(url, pool_size=4, max_overflow=0)
    if not inspect(engine).has_table("interview_rounds"):
        pytest.fail("Run `alembic upgrade head` against the disposable M6 test database first")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    suffix = uuid4().hex
    hr_id, interviewer_id, second_interviewer_id, job_id = uuid4(), uuid4(), uuid4(), uuid4()
    candidate_ids, application_ids = [uuid4(), uuid4()], [uuid4(), uuid4()]
    starts = datetime.now(timezone.utc) + timedelta(days=40)
    round_ids = [uuid4(), uuid4()]
    with factory.begin() as db:
        db.add_all([
            Employee(id=hr_id, full_name="M6 Concurrency HR", email=f"m6hr-{suffix}@example.test",
                     role=EmployeeRole.HR_MANAGER, status=EmployeeStatus.ACTIVE),
            Employee(id=interviewer_id, full_name="M6 Concurrency Interviewer",
                     email=f"m6int-{suffix}@example.test", role=EmployeeRole.INTERVIEWER,
                     status=EmployeeStatus.ACTIVE),
            Employee(id=second_interviewer_id, full_name="M6 Concurrency Interviewer 2",
                     email=f"m6int2-{suffix}@example.test", role=EmployeeRole.INTERVIEWER,
                     status=EmployeeStatus.ACTIVE),
            Job(id=job_id, title="Concurrency Interview Test", department="Engineering",
                employment_type="Full-time", location="Remote", work_mode="Remote",
                description="Disposable concurrency test", vacancies=1, status="published",
                created_by_id=hr_id, updated_by_id=hr_id),
        ])
        for index in range(2):
            candidate = Candidate(id=candidate_ids[index], full_name=f"M6 Test Candidate {index}",
                                  email=f"m6candidate-{index}-{suffix}@example.test")
            db.add(Application(id=application_ids[index], candidate=candidate, job_id=job_id,
                               source="referral", stage="interview"))
        if assignment_mode in ("same_interviewer", "empty_overlapping"):
            for index in range(2):
                round_start = starts + (timedelta(hours=index * 3) if assignment_mode == "same_interviewer"
                                        else timedelta(0))
                round_ = InterviewRound(id=round_ids[index], application_id=application_ids[index],
                                        starts_at=round_start, ends_at=round_start + timedelta(hours=1),
                                        timezone_name="Asia/Kolkata", status="scheduled")
                db.add(round_)
                if assignment_mode == "same_interviewer":
                    db.add(InterviewerAssignment(interview_round_id=round_ids[index],
                                                 interviewer_employee_id=interviewer_id,
                                                 assigned_by_id=hr_id))
    return engine, factory, hr_id, interviewer_id, second_interviewer_id, application_ids, round_ids, starts


def _barrier_call(barrier: Barrier, operation):
    def run():
        barrier.wait(timeout=10)
        return operation()
    return run


@pytest.mark.skipif(not os.environ.get("MILESTONE6_TEST_DATABASE_URL"), reason="requires explicitly named disposable PostgreSQL database")
def test_postgres_serializes_concurrent_interviewer_scheduling() -> None:
    url = os.environ["MILESTONE6_TEST_DATABASE_URL"]
    parsed = make_url(url)
    if parsed.get_backend_name() != "postgresql" or parsed.database != "recruitflow_m6_test":
        pytest.fail("MILESTONE6_TEST_DATABASE_URL must point to the disposable recruitflow_m6_test database")

    engine = create_engine(url, pool_size=2, max_overflow=0)
    if not inspect(engine).has_table("interview_rounds"):
        pytest.fail("Run `alembic upgrade head` against the disposable M6 test database first")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    suffix = uuid4().hex
    hr_id, interviewer_id, job_id = uuid4(), uuid4(), uuid4()
    candidate_ids, application_ids = [uuid4(), uuid4()], [uuid4(), uuid4()]
    starts_at = datetime.now(timezone.utc) + timedelta(days=40)
    try:
        with factory.begin() as db:
            hr = Employee(id=hr_id, full_name="M6 Concurrency HR", email=f"m6hr-{suffix}@example.test",
                          role=EmployeeRole.HR_MANAGER, status=EmployeeStatus.ACTIVE)
            interviewer = Employee(id=interviewer_id, full_name="M6 Concurrency Interviewer",
                                   email=f"m6int-{suffix}@example.test", role=EmployeeRole.INTERVIEWER,
                                   status=EmployeeStatus.ACTIVE)
            job = Job(id=job_id, title="Concurrency Interview Test", department="Engineering",
                      employment_type="Full-time", location="Remote", work_mode="Remote",
                      description="Disposable concurrency test", vacancies=1, status="published",
                      created_by_id=hr_id, updated_by_id=hr_id)
            db.add_all([hr, interviewer, job])
            for index in range(2):
                candidate = Candidate(id=candidate_ids[index], full_name=f"M6 Test Candidate {index}",
                                      email=f"m6candidate-{index}-{suffix}@example.test")
                application = Application(id=application_ids[index], candidate=candidate, job_id=job_id,
                                          source="referral", stage="interview")
                db.add_all([candidate, application])

        barrier = Barrier(2)

        def schedule(application_id):
            with factory() as db:
                db.connection().exec_driver_sql("SET LOCAL lock_timeout = '5s'")
                db.connection().exec_driver_sql("SET LOCAL statement_timeout = '12s'")
                actor = db.get(Employee, hr_id)
                barrier.wait(timeout=15)
                try:
                    create_round(db, application_id=application_id, starts_at=starts_at,
                                 ends_at=starts_at + timedelta(hours=1), timezone_name="Asia/Kolkata",
                                 interviewer_ids=[interviewer_id], actor=actor)
                    return "scheduled"
                except HTTPException as exc:
                    if exc.status_code == 409:
                        return "conflict"
                    raise

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(schedule, application_id) for application_id in application_ids]
            outcomes = [future.result(timeout=20) for future in futures]
        assert sorted(outcomes) == ["conflict", "scheduled"]
        with factory() as db:
            assigned_rounds = db.scalars(select(InterviewerAssignment.interview_round_id).join(
                InterviewRound, InterviewRound.id == InterviewerAssignment.interview_round_id
            ).where(InterviewerAssignment.interviewer_employee_id == interviewer_id,
                    InterviewerAssignment.removed_at.is_(None), InterviewRound.status == "scheduled",
                    InterviewRound.starts_at < starts_at + timedelta(hours=1),
                    InterviewRound.ends_at > starts_at)).all()
            assert len(assigned_rounds) == 1
    finally:
        # No deletes: Phase 1 database triggers intentionally make interview/audit history append-only.
        engine.dispose()


@pytest.mark.skipif(not os.environ.get("MILESTONE6_TEST_DATABASE_URL"), reason="requires disposable PostgreSQL database")
def test_postgres_serializes_concurrent_rescheduling() -> None:
    url = os.environ["MILESTONE6_TEST_DATABASE_URL"]
    engine, factory, hr_id, interviewer_id, _, _, round_ids, starts = _seed(url, assignment_mode="same_interviewer")
    barrier = Barrier(2)

    def move(round_id):
        def operation():
            with factory() as db:
                db.connection().exec_driver_sql("SET LOCAL lock_timeout = '5s'")
                db.connection().exec_driver_sql("SET LOCAL statement_timeout = '12s'")
                try:
                    round_ = db.get(InterviewRound, round_id)
                    reschedule_round(db, round_, starts_at=starts + timedelta(days=1),
                                     ends_at=starts + timedelta(days=1, hours=1),
                                     timezone_name="Asia/Kolkata", actor=db.get(Employee, hr_id))
                    return "rescheduled"
                except HTTPException as exc:
                    db.rollback()
                    if exc.status_code == 409:
                        return "conflict"
                    raise
        return _barrier_call(barrier, operation)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(move(round_id)) for round_id in round_ids]
            outcomes = [future.result(timeout=20) for future in futures]
        assert sorted(outcomes) == ["conflict", "rescheduled"]
        with factory() as db:
            overlapping = db.scalar(select(InterviewRound.id).join(InterviewerAssignment).where(
                InterviewerAssignment.interviewer_employee_id == interviewer_id,
                InterviewerAssignment.removed_at.is_(None), InterviewRound.status == "scheduled",
                InterviewRound.starts_at < starts + timedelta(days=1, hours=1),
                InterviewRound.ends_at > starts + timedelta(days=1),
            ).limit(2))
            assert overlapping is not None
    finally:
        engine.dispose()


@pytest.mark.skipif(not os.environ.get("MILESTONE6_TEST_DATABASE_URL"), reason="requires disposable PostgreSQL database")
def test_postgres_serializes_concurrent_interviewer_additions() -> None:
    url = os.environ["MILESTONE6_TEST_DATABASE_URL"]
    engine, factory, hr_id, interviewer_id, _, _, round_ids, _ = _seed(url, assignment_mode="empty_overlapping")
    barrier = Barrier(2)

    def assign(round_id):
        def operation():
            with factory() as db:
                db.connection().exec_driver_sql("SET LOCAL lock_timeout = '5s'")
                db.connection().exec_driver_sql("SET LOCAL statement_timeout = '12s'")
                try:
                    add_interviewer(db, db.get(InterviewRound, round_id), interviewer_id, db.get(Employee, hr_id))
                    return "assigned"
                except HTTPException as exc:
                    db.rollback()
                    if exc.status_code == 409:
                        return "conflict"
                    raise
        return _barrier_call(barrier, operation)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(assign(round_id)) for round_id in round_ids]
            outcomes = [future.result(timeout=20) for future in futures]
        assert sorted(outcomes) == ["assigned", "conflict"]
        with factory() as db:
            assignments = db.scalars(select(InterviewerAssignment).where(
                InterviewerAssignment.interviewer_employee_id == interviewer_id,
                InterviewerAssignment.removed_at.is_(None),
            )).all()
            assert len(assignments) == 1
    finally:
        engine.dispose()


@pytest.mark.skipif(not os.environ.get("MILESTONE6_TEST_DATABASE_URL"), reason="requires disposable PostgreSQL database")
def test_postgres_locks_multiple_interviewers_in_deterministic_order() -> None:
    url = os.environ["MILESTONE6_TEST_DATABASE_URL"]
    engine, factory, hr_id, interviewer_id, second_interviewer_id, app_ids, _, starts = _seed(url)
    barrier = Barrier(2)

    def schedule(application_id, interviewer_ids, day):
        def operation():
            with factory() as db:
                db.connection().exec_driver_sql("SET LOCAL lock_timeout = '5s'")
                db.connection().exec_driver_sql("SET LOCAL statement_timeout = '12s'")
                actor = db.get(Employee, hr_id)
                try:
                    create_round(db, application_id=application_id,
                                 starts_at=starts + timedelta(days=day),
                                 ends_at=starts + timedelta(days=day, hours=1),
                                 timezone_name="Asia/Kolkata", interviewer_ids=interviewer_ids, actor=actor)
                    return "scheduled"
                except HTTPException as exc:
                    db.rollback()
                    return f"http-{exc.status_code}"
        return _barrier_call(barrier, operation)

    try:
        ids = [interviewer_id, second_interviewer_id]
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(schedule(app_ids[0], ids, 4)),
                       pool.submit(schedule(app_ids[1], list(reversed(ids)), 6))]
            outcomes = [future.result(timeout=20) for future in futures]
        assert outcomes == ["scheduled", "scheduled"]
    except FutureTimeout:
        pytest.fail("Concurrent multi-interviewer scheduling hung (possible lock-order deadlock)")
    finally:
        engine.dispose()


@pytest.mark.skipif(not os.environ.get("MILESTONE6_TEST_DATABASE_URL"), reason="requires disposable PostgreSQL database")
def test_postgres_concurrent_duplicate_feedback_submission() -> None:
    url = os.environ["MILESTONE6_TEST_DATABASE_URL"]
    engine, factory, hr_id, interviewer_id, _, application_ids, round_ids, _ = _seed(url, assignment_mode="same_interviewer")
    with factory() as db:
        assignment = db.scalar(select(InterviewerAssignment).where(
            InterviewerAssignment.interview_round_id == round_ids[0],
            InterviewerAssignment.interviewer_employee_id == interviewer_id,
        ))
        assignment_id = assignment.id
    barrier = Barrier(2)

    def submit():
        with factory() as db:
            db.connection().exec_driver_sql("SET LOCAL lock_timeout = '5s'")
            db.connection().exec_driver_sql("SET LOCAL statement_timeout = '12s'")
            actor = db.get(Employee, interviewer_id)
            barrier.wait(timeout=10)
            try:
                submit_feedback(db, assignment_id, actor, {
                    "rating": 4, "strengths": "Clear reasoning", "areas_for_improvement": "More detail",
                    "recommendation": "hire",
                })
                return "created"
            except HTTPException as exc:
                db.rollback()
                return f"http-{exc.status_code}"

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(submit) for _ in range(2)]
            outcomes = [future.result(timeout=20) for future in futures]
        assert sorted(outcomes) == ["created", "http-409"]
        with factory() as db:
            assert len(db.scalars(select(InterviewFeedback).where(
                InterviewFeedback.interview_round_id == round_ids[0])).all()) == 1
            assert len(db.scalars(select(AuditLog).where(
                AuditLog.action == "interview.feedback_created",
                AuditLog.target_application_id == application_ids[0])).all()) == 1
    except FutureTimeout:
        pytest.fail("Concurrent feedback submissions hung")
    finally:
        engine.dispose()


@pytest.mark.skipif(not os.environ.get("MILESTONE6_TEST_DATABASE_URL"), reason="requires disposable PostgreSQL database")
def test_postgres_concurrent_feedback_edits_preserve_each_revision() -> None:
    url = os.environ["MILESTONE6_TEST_DATABASE_URL"]
    engine, factory, hr_id, interviewer_id, _, _, round_ids, _ = _seed(url, assignment_mode="same_interviewer")
    with factory.begin() as db:
        assignment = db.scalar(select(InterviewerAssignment).where(
            InterviewerAssignment.interview_round_id == round_ids[0],
            InterviewerAssignment.interviewer_employee_id == interviewer_id,
        ))
        feedback = InterviewFeedback(interview_round_id=round_ids[0], interviewer_employee_id=interviewer_id,
                                     rating=3, strengths="Original", areas_for_improvement="Original improvement",
                                     recommendation="hire")
        db.add(feedback)
        db.flush()
        feedback_id, assignment_id = feedback.id, assignment.id
    barrier = Barrier(2)

    def edit(value):
        with factory() as db:
            db.connection().exec_driver_sql("SET LOCAL lock_timeout = '5s'")
            db.connection().exec_driver_sql("SET LOCAL statement_timeout = '12s'")
            actor = db.get(Employee, interviewer_id)
            barrier.wait(timeout=10)
            edit_feedback(db, assignment_id, actor, {"strengths": value})
            return "edited"

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(edit, "First concurrent revision"), pool.submit(edit, "Second concurrent revision")]
            assert [future.result(timeout=20) for future in futures] == ["edited", "edited"]
        with factory() as db:
            revisions = db.scalars(select(InterviewFeedbackRevision).where(
                InterviewFeedbackRevision.feedback_id == feedback_id)).all()
            assert len(revisions) == 2
            assert {revision.strengths for revision in revisions} == {"Original", "First concurrent revision"} or \
                   {revision.strengths for revision in revisions} == {"Original", "Second concurrent revision"}
            current = db.get(InterviewFeedback, feedback_id)
            assert current.strengths in {"First concurrent revision", "Second concurrent revision"}
    except FutureTimeout:
        pytest.fail("Concurrent feedback edits hung")
    finally:
        engine.dispose()


@pytest.mark.skipif(not os.environ.get("MILESTONE6_TEST_DATABASE_URL"), reason="requires disposable PostgreSQL database")
def test_postgres_feedback_edit_racing_round_completion_is_atomic() -> None:
    url = os.environ["MILESTONE6_TEST_DATABASE_URL"]
    engine, factory, hr_id, interviewer_id, _, _, round_ids, _ = _seed(url, assignment_mode="same_interviewer")
    with factory.begin() as db:
        assignment = db.scalar(select(InterviewerAssignment).where(
            InterviewerAssignment.interview_round_id == round_ids[0],
            InterviewerAssignment.interviewer_employee_id == interviewer_id,
        ))
        feedback = InterviewFeedback(interview_round_id=round_ids[0], interviewer_employee_id=interviewer_id,
                                     rating=3, strengths="Original", areas_for_improvement="Original improvement",
                                     recommendation="hire")
        db.add(feedback)
        db.flush()
        assignment_id, feedback_id = assignment.id, feedback.id
    barrier = Barrier(2)

    def edit():
        with factory() as db:
            db.connection().exec_driver_sql("SET LOCAL lock_timeout = '5s'")
            db.connection().exec_driver_sql("SET LOCAL statement_timeout = '12s'")
            barrier.wait(timeout=10)
            try:
                edit_feedback(db, assignment_id, db.get(Employee, interviewer_id), {"rating": 5})
                return "edited"
            except HTTPException as exc:
                db.rollback()
                return f"http-{exc.status_code}"

    def complete():
        with factory() as db:
            db.connection().exec_driver_sql("SET LOCAL lock_timeout = '5s'")
            db.connection().exec_driver_sql("SET LOCAL statement_timeout = '12s'")
            barrier.wait(timeout=10)
            close_round(db, db.get(InterviewRound, round_ids[0]), target="completed", actor=db.get(Employee, hr_id))
            return "completed"

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(edit), pool.submit(complete)]
            outcomes = [future.result(timeout=20) for future in futures]
        assert "completed" in outcomes
        assert outcomes.count("edited") + outcomes.count("http-409") == 1
        with factory() as db:
            assert db.get(InterviewRound, round_ids[0]).status == "completed"
            revisions = db.scalars(select(InterviewFeedbackRevision).where(
                InterviewFeedbackRevision.feedback_id == feedback_id)).all()
            current = db.get(InterviewFeedback, feedback_id)
            if "edited" in outcomes:
                assert len(revisions) == 1 and revisions[0].rating == 3 and current.rating == 5
            else:
                assert revisions == [] and current.rating == 3
    except FutureTimeout:
        pytest.fail("Feedback edit versus completion hung")
    finally:
        engine.dispose()
