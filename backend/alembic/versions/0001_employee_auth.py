"""Add employee authentication and RBAC tables.

Revision ID: 0001_employee_auth
Revises:
Create Date: 2026-11-05
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0001_employee_auth"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

employee_role = sa.Enum(
    "admin", "hr_manager", "recruiter", "interviewer",
    name="employee_role", native_enum=False, create_constraint=True,
)
invitation_role = sa.Enum(
    "admin", "hr_manager", "recruiter", "interviewer",
    name="invitation_role", native_enum=False, create_constraint=True,
)
employee_status = sa.Enum(
    "invited", "active", "disabled",
    name="employee_status", native_enum=False, create_constraint=True,
)


def upgrade() -> None:
    op.create_table(
        "employees",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("role", employee_role, nullable=False),
        sa.Column("status", employee_status, nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("length(email) > 2", name="ck_employee_email_nonempty"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_employees_email"),
    )
    op.create_index("ix_employees_status_role", "employees", ["status", "role"])

    op.create_table(
        "employee_invitations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("role", invitation_role, nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invited_by_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["invited_by_id"], ["employees.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_employee_invitations_token_hash"),
    )
    op.create_index("ix_employee_invitations_email_expires", "employee_invitations", ["email", "expires_at"])

    op.create_table(
        "refresh_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_refresh_sessions_token_hash"),
    )
    op.create_index("ix_refresh_sessions_employee_active", "refresh_sessions", ["employee_id", "revoked_at"])

    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_password_reset_tokens_token_hash"),
    )
    op.create_index("ix_password_reset_employee_expiry", "password_reset_tokens", ["employee_id", "expires_at"])


def downgrade() -> None:
    op.drop_index("ix_password_reset_employee_expiry", table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")
    op.drop_index("ix_refresh_sessions_employee_active", table_name="refresh_sessions")
    op.drop_table("refresh_sessions")
    op.drop_index("ix_employee_invitations_email_expires", table_name="employee_invitations")
    op.drop_table("employee_invitations")
    op.drop_index("ix_employees_status_role", table_name="employees")
    op.drop_table("employees")
