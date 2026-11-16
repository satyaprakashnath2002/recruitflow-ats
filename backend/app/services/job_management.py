from sqlalchemy.orm import Session

from app.core.security import utcnow
from app.models.auth import AuditLog, Employee
from app.models.jobs import Job

JOB_TRANSITIONS = {
    "draft": {"published"},
    "published": {"paused", "closed"},
    "paused": {"published", "closed"},
    "closed": {"archived"},
    "archived": set(),
}

JOB_STATUS_ACTIONS = {
    "published": "job.published",
    "paused": "job.paused",
    "closed": "job.closed",
    "archived": "job.archived",
}


def record_job_audit(
    db: Session,
    *,
    actor: Employee,
    job: Job,
    action: str,
    details: dict[str, object],
) -> None:
    db.add(
        AuditLog(
            actor_employee_id=actor.id,
            target_job_id=job.id,
            action=action,
            details=details,
            created_at=utcnow(),
        )
    )
