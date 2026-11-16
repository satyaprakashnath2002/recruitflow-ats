"""Add persistent in-app notifications and interview reminders.

Revision ID: 0009_in_app_notifications
Revises: 0008_interview_feedback_audit
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0009_in_app_notifications"
down_revision: str | None = "0008_interview_feedback_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("recipient_employee_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("body", sa.String(length=500), nullable=False),
        sa.Column("resource_type", sa.String(length=40), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "event_type IN ('interview_scheduled', 'interview_rescheduled', 'interview_cancelled', "
            "'interview_completed', 'interview_reminder_24h', 'interview_reminder_1h')",
            name="ck_notifications_event_type",
        ),
        sa.ForeignKeyConstraint(["recipient_employee_id"], ["employees.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["resource_id"], ["interview_rounds.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_notifications_idempotency_key"),
    )
    op.create_index("ix_notifications_recipient_created", "notifications", ["recipient_employee_id", "created_at"])
    op.create_index(
        "ix_notifications_recipient_read_created",
        "notifications",
        ["recipient_employee_id", "read_at", "created_at"],
    )
    op.create_table(
        "scheduled_reminders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("interview_round_id", sa.Uuid(), nullable=False),
        sa.Column("interviewer_employee_id", sa.Uuid(), nullable=False),
        sa.Column("offset_minutes", sa.Integer(), nullable=False),
        sa.Column("starts_at_snapshot", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.CheckConstraint("offset_minutes IN (60, 1440)", name="ck_scheduled_reminders_offset"),
        sa.CheckConstraint("state IN ('pending', 'delivered', 'superseded')", name="ck_scheduled_reminders_state"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_scheduled_reminders_attempts"),
        sa.ForeignKeyConstraint(["interview_round_id"], ["interview_rounds.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["interviewer_employee_id"], ["employees.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_scheduled_reminders_idempotency_key"),
    )
    op.create_index("ix_scheduled_reminders_due", "scheduled_reminders", ["state", "scheduled_at"])


def downgrade() -> None:
    bind = op.get_bind()
    notification_count = bind.execute(sa.text("SELECT count(*) FROM notifications")).scalar_one()
    reminder_count = bind.execute(sa.text("SELECT count(*) FROM scheduled_reminders")).scalar_one()
    if notification_count or reminder_count:
        raise RuntimeError(
            "Cannot downgrade while notification or reminder history exists; refusing to delete user-visible records"
        )
    op.drop_index("ix_scheduled_reminders_due", table_name="scheduled_reminders")
    op.drop_table("scheduled_reminders")
    op.drop_index("ix_notifications_recipient_read_created", table_name="notifications")
    op.drop_index("ix_notifications_recipient_created", table_name="notifications")
    op.drop_table("notifications")

