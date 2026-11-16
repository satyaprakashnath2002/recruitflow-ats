"""Add employee names and append-only employee management audit events.

Revision ID: 0002_employee_management
Revises: 0001_employee_auth
Create Date: 2026-11-05
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0002_employee_management"
down_revision: str | None = "0001_employee_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("employees", sa.Column("full_name", sa.String(length=200), nullable=True))
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_employee_id", sa.Uuid(), nullable=False),
        sa.Column("target_employee_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "action IN ('employee.updated', 'employee.role_changed', 'employee.activated', 'employee.deactivated')",
            name="ck_audit_log_employee_action",
        ),
        sa.ForeignKeyConstraint(["actor_employee_id"], ["employees.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["target_employee_id"], ["employees.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_target_created", "audit_logs", ["target_employee_id", "created_at"])
    op.create_index("ix_audit_logs_actor_created", "audit_logs", ["actor_employee_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_actor_created", table_name="audit_logs")
    op.drop_index("ix_audit_logs_target_created", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_column("employees", "full_name")
