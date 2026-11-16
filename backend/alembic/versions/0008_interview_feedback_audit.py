"""Allow approved interview feedback audit events.

Revision ID: 0008_interview_feedback_audit
Revises: 0007_terminal_immutable
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0008_interview_feedback_audit"
down_revision: str | None = "0007_terminal_immutable"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTIONS = (
    "'employee.updated', 'employee.role_changed', 'employee.activated', 'employee.deactivated', "
    "'job.created', 'job.updated', 'job.published', 'job.paused', 'job.closed', 'job.archived', "
    "'job.recruiter_assigned', 'job.recruiter_removed', 'candidate.created', 'candidate.updated', "
    "'application.created', 'application.updated', 'application.stage_changed', 'resume.uploaded', "
    "'resume.replaced', 'resume.downloaded', 'application.note_created', 'application.created_after_deadline', "
    "'interview.round_created', 'interview.scheduled', 'interview.rescheduled', 'interview.cancelled', "
    "'interview.completed', 'interview.interviewer_assigned', 'interview.interviewer_removed', "
    "'interview.feedback_created', 'interview.feedback_modified'"
)
_PREVIOUS_ACTIONS = _ACTIONS.removesuffix(
    ", 'interview.feedback_created', 'interview.feedback_modified'"
)


def _replace_check(actions: str) -> None:
    with op.batch_alter_table("audit_logs") as batch:
        batch.drop_constraint("ck_audit_log_action", type_="check")
        batch.create_check_constraint("ck_audit_log_action", f"action IN ({actions})")


def upgrade() -> None:
    _replace_check(_ACTIONS)


def downgrade() -> None:
    bind = op.get_bind()
    used = bind.execute(sa.text(
        "SELECT count(*) FROM audit_logs WHERE action IN "
        "('interview.feedback_created', 'interview.feedback_modified')"
    )).scalar_one()
    if used:
        raise RuntimeError(
            "Cannot downgrade while interview feedback audit events exist; refusing to invalidate audit history"
        )
    _replace_check(_PREVIOUS_ACTIONS)
