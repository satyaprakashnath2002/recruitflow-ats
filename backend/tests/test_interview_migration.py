import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
import uuid

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
LATEST_REVISION = "0010_offer_management"
PREVIOUS_REVISION = "0006_interview_scheduling_audit"
FEEDBACK_DOWNGRADE_TARGET = "0007_terminal_immutable"
DATA_GUARD_REVISION = "0004_candidate_application_core"
AUDIT_DOWNGRADE_TARGET = "0005_interview_data_model"


def run_alembic(database_url: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "test",
            "DATABASE_URL": database_url,
            "JWT_SECRET": "test-only-signing-key-not-for-real-environments-12345",
        }
    )
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=BACKEND_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def test_interview_migration_upgrade_check_downgrade_and_data_guard(tmp_path: Path) -> None:
    database_path = tmp_path / "interview-migration-test.db"
    database_url = f"sqlite:///{database_path.as_posix()}"

    upgraded = run_alembic(database_url, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr

    current = run_alembic(database_url, "current")
    assert current.returncode == 0, current.stdout + current.stderr
    assert LATEST_REVISION in current.stdout

    checked = run_alembic(database_url, "check")
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "No new upgrade operations detected" in checked.stdout + checked.stderr

    clean_downgrade = run_alembic(database_url, "downgrade", "0009_in_app_notifications")
    assert clean_downgrade.returncode == 0, clean_downgrade.stdout + clean_downgrade.stderr
    reupgraded = run_alembic(database_url, "upgrade", "head")
    assert reupgraded.returncode == 0, reupgraded.stdout + reupgraded.stderr

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO interview_rounds "
            "(id, application_id, starts_at, ends_at, timezone_name, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "00000000-0000-0000-0000-000000000001",
                "00000000-0000-0000-0000-000000000002",
                "2030-01-01T10:00:00+00:00",
                "2030-01-01T11:00:00+00:00",
                "UTC",
                "scheduled",
            ),
        )
        connection.commit()

    refused_downgrade = run_alembic(database_url, "downgrade", DATA_GUARD_REVISION)
    assert refused_downgrade.returncode != 0
    assert "refusing to delete Milestone 6 interview history" in (
        refused_downgrade.stdout + refused_downgrade.stderr
    )

    current_after_refusal = run_alembic(database_url, "current")
    assert current_after_refusal.returncode == 0, current_after_refusal.stdout + current_after_refusal.stderr
    # The safe 0006 downgrade completed before 0005's data-loss guard refused.
    assert "0005_interview_data_model" in current_after_refusal.stdout
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT count(*) FROM interview_rounds").fetchone()[0] == 1
    restored = run_alembic(database_url, "upgrade", "head")
    assert restored.returncode == 0, restored.stdout + restored.stderr
    restored_check = run_alembic(database_url, "check")
    assert restored_check.returncode == 0, restored_check.stdout + restored_check.stderr


def test_sqlite_migration_integrity_triggers(tmp_path: Path) -> None:
    database_path = tmp_path / "interview-trigger-test.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    upgraded = run_alembic(database_url, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr

    admin_id, interviewer_id, recruiter_id, other_interviewer_id = (str(uuid.uuid4()) for _ in range(4))
    candidate_id, job_id, application_id = (str(uuid.uuid4()) for _ in range(3))
    round_id, closed_round_id = (str(uuid.uuid4()) for _ in range(2))
    start = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    end = (datetime.now(timezone.utc) + timedelta(days=2, hours=1)).isoformat()
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO employees (id,email,role,status) VALUES (?,?,?,?)",
            (admin_id, "m6-admin@example.test", "admin", "active"),
        )
        connection.execute(
            "INSERT INTO employees (id,email,role,status) VALUES (?,?,?,?)",
            (interviewer_id, "m6-interviewer@example.test", "interviewer", "active"),
        )
        connection.execute(
            "INSERT INTO employees (id,email,role,status) VALUES (?,?,?,?)",
            (recruiter_id, "m6-recruiter@example.test", "recruiter", "active"),
        )
        connection.execute(
            "INSERT INTO employees (id,email,role,status) VALUES (?,?,?,?)",
            (other_interviewer_id, "m6-interviewer2@example.test", "interviewer", "active"),
        )
        connection.execute(
            "INSERT INTO candidates (id,full_name,email) VALUES (?,?,?)",
            (candidate_id, "M6 Candidate", "candidate@example.test"),
        )
        connection.execute(
            "INSERT INTO jobs "
            "(id,title,department,employment_type,location,work_mode,description,required_skills,qualifications,"
            "vacancies,status,created_by_id,updated_by_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                job_id, "Engineer", "Engineering", "Full-time", "Bengaluru", "Hybrid", "Build software.",
                "[]", "[]", 1, "published", admin_id, admin_id,
            ),
        )
        connection.execute(
            "INSERT INTO applications (id,candidate_id,job_id,source,stage) VALUES (?,?,?,?,?)",
            (application_id, candidate_id, job_id, "referral", "interview"),
        )
        for identifier in (round_id, closed_round_id):
            connection.execute(
                "INSERT INTO interview_rounds "
                "(id,application_id,starts_at,ends_at,timezone_name,status) VALUES (?,?,?,?,?,?)",
                (identifier, application_id, start, end, "Asia/Kolkata", "scheduled"),
            )
        connection.execute(
            "INSERT INTO interviewer_assignments "
            "(id,interview_round_id,interviewer_employee_id,assigned_by_id) VALUES (?,?,?,?)",
            (str(uuid.uuid4()), round_id, interviewer_id, admin_id),
        )

        with pytest.raises(sqlite3.IntegrityError, match="active Interviewer"):
            connection.execute(
                "INSERT INTO interviewer_assignments "
                "(id,interview_round_id,interviewer_employee_id,assigned_by_id) VALUES (?,?,?,?)",
                (str(uuid.uuid4()), round_id, recruiter_id, admin_id),
            )

        with pytest.raises(sqlite3.IntegrityError, match="future Scheduled"):
            connection.execute("UPDATE employees SET status='disabled' WHERE id=?", (interviewer_id,))

        connection.execute(
            "UPDATE interviewer_assignments SET removed_at=?, removed_by_id=? "
            "WHERE interview_round_id=? AND interviewer_employee_id=?",
            (datetime.now(timezone.utc).isoformat(), admin_id, round_id, interviewer_id),
        )
        connection.execute("UPDATE employees SET status='disabled' WHERE id=?", (interviewer_id,))

        connection.execute("UPDATE interview_rounds SET status='completed' WHERE id=?", (closed_round_id,))
        for fields in (
            "starts_at='2031-01-01T10:00:00+00:00'",
            "ends_at='2031-01-01T12:00:00+00:00'",
            "timezone_name='UTC'",
            "status='cancelled'",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="terminal interview rounds are immutable"):
                connection.execute(f"UPDATE interview_rounds SET {fields} WHERE id=?", (closed_round_id,))
        with pytest.raises(sqlite3.IntegrityError, match="assignments are locked"):
            connection.execute(
                "INSERT INTO interviewer_assignments "
                "(id,interview_round_id,interviewer_employee_id,assigned_by_id) VALUES (?,?,?,?)",
                (str(uuid.uuid4()), closed_round_id, other_interviewer_id, admin_id),
            )
        with pytest.raises(sqlite3.IntegrityError, match="terminal interview rounds are immutable"):
            connection.execute(
                "UPDATE interview_rounds SET status='cancelled' WHERE id=?", (closed_round_id,)
            )
        with pytest.raises(sqlite3.IntegrityError, match="interview history is retained"):
            connection.execute("DELETE FROM interview_rounds WHERE id=?", (closed_round_id,))


def test_scheduling_audit_migration_refuses_downgrade_with_events(tmp_path: Path) -> None:
    database_path = tmp_path / "interview-audit-downgrade-test.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    upgraded = run_alembic(database_url, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO audit_logs (id, target_application_id, actor_kind, action, details) "
            "VALUES (?, ?, 'public', 'interview.round_created', '{}')",
            (str(uuid.uuid4()), str(uuid.uuid4())),
        )
        connection.commit()

    refused = run_alembic(database_url, "downgrade", AUDIT_DOWNGRADE_TARGET)
    assert refused.returncode != 0
    assert "interview scheduling audit events exist" in refused.stdout + refused.stderr
    current = run_alembic(database_url, "current")
    assert current.returncode == 0, current.stdout + current.stderr
    assert PREVIOUS_REVISION in current.stdout


def test_feedback_audit_migration_round_trip_and_data_guard(tmp_path: Path) -> None:
    database_path = tmp_path / "interview-feedback-audit-migration-test.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    upgraded = run_alembic(database_url, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
    checked = run_alembic(database_url, "check")
    assert checked.returncode == 0, checked.stdout + checked.stderr
    downgraded = run_alembic(database_url, "downgrade", FEEDBACK_DOWNGRADE_TARGET)
    assert downgraded.returncode == 0, downgraded.stdout + downgraded.stderr
    reupgraded = run_alembic(database_url, "upgrade", "head")
    assert reupgraded.returncode == 0, reupgraded.stdout + reupgraded.stderr

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO audit_logs (id, target_application_id, actor_kind, action, details) "
            "VALUES (?, ?, 'public', 'interview.feedback_created', '{}')",
            (str(uuid.uuid4()), str(uuid.uuid4())),
        )
        connection.commit()
    refused = run_alembic(database_url, "downgrade", FEEDBACK_DOWNGRADE_TARGET)
    assert refused.returncode != 0
    assert "interview feedback audit events exist" in refused.stdout + refused.stderr


def test_notification_migration_downgrade_refuses_to_discard_notification_history(tmp_path: Path) -> None:
    database_path = tmp_path / "notification-migration-test.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    upgraded = run_alembic(database_url, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO notifications "
            "(id, recipient_employee_id, event_type, title, body, resource_type, resource_id, idempotency_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), str(uuid.uuid4()), "interview_scheduled", "Scheduled", "Time", "interview_round",
             str(uuid.uuid4()), str(uuid.uuid4())),
        )
        connection.commit()

    refused = run_alembic(database_url, "downgrade", "0008_interview_feedback_audit")
    assert refused.returncode != 0
    assert "refusing to delete user-visible records" in refused.stdout + refused.stderr
    current = run_alembic(database_url, "current")
    # Migration 0010 downgrades cleanly first; 0009 then refuses to discard
    # the notification row and leaves that history intact.
    assert current.returncode == 0 and "0009_in_app_notifications" in current.stdout
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT count(*) FROM notifications").fetchone()[0] == 1


def test_offer_migration_round_trip_and_data_guard(tmp_path: Path) -> None:
    database_path = tmp_path / "offer-migration-test.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    upgraded = run_alembic(database_url, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
    checked = run_alembic(database_url, "check")
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "No new upgrade operations detected" in checked.stdout + checked.stderr

    clean_downgrade = run_alembic(database_url, "downgrade", "0009_in_app_notifications")
    assert clean_downgrade.returncode == 0, clean_downgrade.stdout + clean_downgrade.stderr
    reupgraded = run_alembic(database_url, "upgrade", "head")
    assert reupgraded.returncode == 0, reupgraded.stdout + reupgraded.stderr

    offer_id = str(uuid.uuid4())
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO offers (id, application_id, created_by_employee_id, status, current_revision_number) "
            "VALUES (?, ?, ?, 'draft', 1)",
            (offer_id, str(uuid.uuid4()), str(uuid.uuid4())),
        )
        connection.commit()

    refused = run_alembic(database_url, "downgrade", "0009_in_app_notifications")
    assert refused.returncode != 0
    assert "Cannot downgrade while Offer" in refused.stdout + refused.stderr
    current = run_alembic(database_url, "current")
    assert current.returncode == 0 and LATEST_REVISION in current.stdout
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT count(*) FROM offers WHERE id=?", (offer_id,)).fetchone()[0] == 1
    restored_check = run_alembic(database_url, "check")
    assert restored_check.returncode == 0, restored_check.stdout + restored_check.stderr
