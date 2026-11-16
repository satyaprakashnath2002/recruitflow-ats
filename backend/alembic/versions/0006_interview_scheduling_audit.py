"""Allow approved Phase 2 interview scheduling audit events.

Revision ID: 0006_interview_scheduling_audit
Revises: 0005_interview_data_model
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0006_interview_scheduling_audit"
down_revision: str | None = "0005_interview_data_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_ACTIONS = (
    "'employee.updated', 'employee.role_changed', 'employee.activated', 'employee.deactivated', "
    "'job.created', 'job.updated', 'job.published', 'job.paused', 'job.closed', 'job.archived', "
    "'job.recruiter_assigned', 'job.recruiter_removed', 'candidate.created', 'candidate.updated', "
    "'application.created', 'application.updated', 'application.stage_changed', 'resume.uploaded', "
    "'resume.replaced', 'resume.downloaded', 'application.note_created', 'application.created_after_deadline'"
)
_INTERVIEW_ACTIONS = (
    "'interview.round_created', 'interview.scheduled', 'interview.rescheduled', "
    "'interview.cancelled', 'interview.completed', 'interview.interviewer_assigned', "
    "'interview.interviewer_removed'"
)


def _replace_check(actions: str) -> None:
    with op.batch_alter_table("audit_logs") as batch:
        batch.drop_constraint("ck_audit_log_action", type_="check")
        batch.create_check_constraint("ck_audit_log_action", f"action IN ({actions})")


def upgrade() -> None:
    _replace_check(f"{_OLD_ACTIONS}, {_INTERVIEW_ACTIONS}")


def downgrade() -> None:
    bind = op.get_bind()
    used = bind.execute(sa.text(
        "SELECT count(*) FROM audit_logs WHERE action LIKE 'interview.%'"
    )).scalar_one()
    if used:
        raise RuntimeError(
            "Cannot downgrade while interview scheduling audit events exist; refusing to invalidate audit history"
        )
    _replace_check(_OLD_ACTIONS)
