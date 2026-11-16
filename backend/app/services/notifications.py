"""In-app interview notification and reminder persistence.

Reminder processing is database-only and safe to invoke from concurrent worker
processes: PostgreSQL row locks plus unique idempotency keys protect delivery.
"""

from datetime import datetime, timedelta, timezone
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import utcnow
from app.models.auth import Employee, EmployeeStatus
from app.models.interviews import InterviewRound, InterviewerAssignment
from app.models.notifications import Notification, ScheduledReminder

logger = logging.getLogger(__name__)
REMINDER_OFFSETS = (1440, 60)


def _active_interviewers(db: Session, round_id: uuid.UUID) -> list[uuid.UUID]:
    return list(db.scalars(
        select(InterviewerAssignment.interviewer_employee_id)
        .join(Employee, Employee.id == InterviewerAssignment.interviewer_employee_id)
        .where(
            InterviewerAssignment.interview_round_id == round_id,
            InterviewerAssignment.removed_at.is_(None),
            Employee.status == EmployeeStatus.ACTIVE,
        )
        .order_by(InterviewerAssignment.interviewer_employee_id)
    ).all())


def create_round_event(
    db: Session, round_: InterviewRound, event_type: str, *, recipients: list[uuid.UUID] | None = None
) -> None:
    if event_type not in ("interview_scheduled", "interview_rescheduled", "interview_cancelled", "interview_completed"):
        raise ValueError("Unsupported interview notification event")
    starts = round_.starts_at
    event_time = utcnow().isoformat()
    for employee_id in recipients if recipients is not None else _active_interviewers(db, round_.id):
        key = f"interview:{round_.id}:{event_type}:{starts.isoformat()}:{event_time}:{employee_id}"
        title = {
            "interview_scheduled": "Interview scheduled",
            "interview_rescheduled": "Interview rescheduled",
            "interview_cancelled": "Interview cancelled",
            "interview_completed": "Interview completed",
        }[event_type]
        body = f"Interview time: {starts.isoformat()} ({round_.timezone_name})"
        db.add(Notification(
            recipient_employee_id=employee_id,
            event_type=event_type,
            title=title,
            body=body,
            resource_type="interview_round",
            resource_id=round_.id,
            idempotency_key=key,
        ))


def schedule_round_reminders(db: Session, round_: InterviewRound) -> None:
    """Supersede obsolete pending reminders and create current schedule jobs."""
    now = utcnow()
    for reminder in db.scalars(select(ScheduledReminder).where(
        ScheduledReminder.interview_round_id == round_.id,
        ScheduledReminder.state == "pending",
    ).with_for_update()):
        reminder.state = "superseded"
    if round_.status != "scheduled":
        return
    for employee_id in _active_interviewers(db, round_.id):
        for offset in REMINDER_OFFSETS:
            due_at = round_.starts_at - timedelta(minutes=offset)
            if _as_utc(due_at) <= now:
                continue
            key = f"interview-reminder:{round_.id}:{employee_id}:{offset}:{round_.starts_at.isoformat()}"
            existing = db.scalar(select(ScheduledReminder).where(ScheduledReminder.idempotency_key == key))
            if existing is not None:
                if existing.state == "superseded":
                    existing.state = "pending"
                    existing.scheduled_at = due_at
                    existing.last_error = None
                continue
            db.add(ScheduledReminder(
                interview_round_id=round_.id,
                interviewer_employee_id=employee_id,
                offset_minutes=offset,
                starts_at_snapshot=round_.starts_at,
                scheduled_at=due_at,
                idempotency_key=key,
            ))


def supersede_interviewer_reminders(db: Session, round_id: uuid.UUID, employee_id: uuid.UUID) -> None:
    reminders = db.scalars(select(ScheduledReminder).where(
        ScheduledReminder.interview_round_id == round_id,
        ScheduledReminder.interviewer_employee_id == employee_id,
        ScheduledReminder.state == "pending",
    ).with_for_update())
    for reminder in reminders:
        reminder.state = "superseded"


def process_due_reminders(db: Session, *, now: datetime | None = None, limit: int = 100) -> int:
    """Persist due in-app reminders. The caller commits/rolls back its session."""
    current = now or utcnow()
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Reminder processing time must be timezone-aware")
    current = current.astimezone(timezone.utc)
    due = db.scalars(
        select(ScheduledReminder)
        .where(ScheduledReminder.state == "pending", ScheduledReminder.scheduled_at <= current)
        .order_by(ScheduledReminder.scheduled_at, ScheduledReminder.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    delivered = 0
    for reminder in due:
        reminder.attempt_count += 1
        reminder.last_attempt_at = current
        try:
            with db.begin_nested():
                round_ = db.get(InterviewRound, reminder.interview_round_id)
                assignment = db.scalar(select(InterviewerAssignment).where(
                    InterviewerAssignment.interview_round_id == reminder.interview_round_id,
                    InterviewerAssignment.interviewer_employee_id == reminder.interviewer_employee_id,
                    InterviewerAssignment.removed_at.is_(None),
                ))
                employee = db.get(Employee, reminder.interviewer_employee_id)
                if (
                    round_ is None or round_.status != "scheduled"
                    or _as_utc(round_.starts_at) != _as_utc(reminder.starts_at_snapshot)
                    or assignment is None or employee is None or employee.status != EmployeeStatus.ACTIVE
                ):
                    reminder.state = "superseded"
                    reminder.last_error = None
                    continue
                event_type = "interview_reminder_24h" if reminder.offset_minutes == 1440 else "interview_reminder_1h"
                db.add(Notification(
                    recipient_employee_id=reminder.interviewer_employee_id,
                    event_type=event_type,
                    title="Upcoming interview reminder",
                    body=f"Interview starts at {round_.starts_at.isoformat()} ({round_.timezone_name})",
                    resource_type="interview_round",
                    resource_id=round_.id,
                    idempotency_key=f"notification:{reminder.id}",
                ))
                db.flush()
                reminder.state = "delivered"
                reminder.last_error = None
                delivered += 1
        except Exception:
            # No external side effect occurs before the DB commit. Pending rows
            # remain retryable on the next worker invocation; error text is
            # intentionally generic and contains no candidate/interview data.
            logger.warning("Failed to persist an in-app interview reminder; it remains pending for retry")
            reminder.last_error = "persistence_failed"
    db.commit()
    return delivered


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

