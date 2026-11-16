"""Add jobs, Recruiter assignments, and job audit targets.

Revision ID: 0003_job_management
Revises: 0002_employee_management
Create Date: 2026-11-06
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0003_job_management"
down_revision: str | None = "0002_employee_management"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOB_ACTIONS = (
    "'employee.updated', 'employee.role_changed', 'employee.activated', 'employee.deactivated', "
    "'job.created', 'job.updated', 'job.published', 'job.paused', 'job.closed', 'job.archived', "
    "'job.recruiter_assigned', 'job.recruiter_removed'"
)


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("department", sa.String(length=120), nullable=False),
        sa.Column("employment_type", sa.String(length=50), nullable=False),
        sa.Column("location", sa.String(length=200), nullable=False),
        sa.Column("work_mode", sa.String(length=50), nullable=False),
        sa.Column("min_experience", sa.Numeric(5, 2), nullable=True),
        sa.Column("max_experience", sa.Numeric(5, 2), nullable=True),
        sa.Column("salary_min", sa.Numeric(12, 2), nullable=True),
        sa.Column("salary_max", sa.Numeric(12, 2), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("required_skills", sa.JSON(), nullable=False),
        sa.Column("qualifications", sa.JSON(), nullable=False),
        sa.Column("vacancies", sa.Integer(), nullable=False),
        sa.Column("application_deadline", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column("updated_by_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(title) > 0", name="ck_jobs_title_nonempty"),
        sa.CheckConstraint("length(department) > 0", name="ck_jobs_department_nonempty"),
        sa.CheckConstraint("length(employment_type) > 0", name="ck_jobs_employment_type_nonempty"),
        sa.CheckConstraint("length(location) > 0", name="ck_jobs_location_nonempty"),
        sa.CheckConstraint("length(work_mode) > 0", name="ck_jobs_work_mode_nonempty"),
        sa.CheckConstraint("length(description) > 0", name="ck_jobs_description_nonempty"),
        sa.CheckConstraint("vacancies > 0", name="ck_jobs_vacancies_positive"),
        sa.CheckConstraint("min_experience IS NULL OR min_experience >= 0", name="ck_jobs_min_experience_nonnegative"),
        sa.CheckConstraint("max_experience IS NULL OR max_experience >= 0", name="ck_jobs_max_experience_nonnegative"),
        sa.CheckConstraint(
            "min_experience IS NULL OR max_experience IS NULL OR min_experience <= max_experience",
            name="ck_jobs_experience_range",
        ),
        sa.CheckConstraint("salary_min IS NULL OR salary_min >= 0", name="ck_jobs_salary_min_nonnegative"),
        sa.CheckConstraint("salary_max IS NULL OR salary_max >= 0", name="ck_jobs_salary_max_nonnegative"),
        sa.CheckConstraint(
            "salary_min IS NULL OR salary_max IS NULL OR salary_min <= salary_max", name="ck_jobs_salary_range"
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'paused', 'closed', 'archived')", name="ck_jobs_status"
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["employees.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["employees.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jobs_status_created", "jobs", ["status", "created_at"])
    op.create_index("ix_jobs_department", "jobs", ["department"])
    op.create_index("ix_jobs_employment_type", "jobs", ["employment_type"])
    op.create_index("ix_jobs_location", "jobs", ["location"])
    op.create_index("ix_jobs_work_mode", "jobs", ["work_mode"])
    op.create_index("ix_jobs_experience", "jobs", ["min_experience", "max_experience"])
    op.create_index("ix_jobs_deadline", "jobs", ["application_deadline"])

    op.create_table(
        "job_assignments",
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_by_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assigned_by_id"], ["employees.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("job_id", "employee_id"),
    )
    op.create_index("ix_job_assignments_employee", "job_assignments", ["employee_id"])

    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("audit_logs", recreate="always") as batch:
            batch.drop_constraint("ck_audit_log_employee_action", type_="check")
            batch.alter_column("target_employee_id", existing_type=sa.Uuid(), nullable=True)
            batch.add_column(sa.Column("target_job_id", sa.Uuid(), nullable=True))
            batch.create_foreign_key(
                "fk_audit_logs_target_job_id_jobs", "jobs", ["target_job_id"], ["id"], ondelete="RESTRICT"
            )
            batch.create_check_constraint("ck_audit_log_action", f"action IN ({JOB_ACTIONS})")
            batch.create_check_constraint(
                "ck_audit_log_single_target",
                "(target_employee_id IS NOT NULL AND target_job_id IS NULL) OR "
                "(target_employee_id IS NULL AND target_job_id IS NOT NULL)",
            )
    else:
        op.drop_constraint("ck_audit_log_employee_action", "audit_logs", type_="check")
        op.alter_column("audit_logs", "target_employee_id", existing_type=sa.Uuid(), nullable=True)
        op.add_column("audit_logs", sa.Column("target_job_id", sa.Uuid(), nullable=True))
        op.create_foreign_key(
            "fk_audit_logs_target_job_id_jobs", "audit_logs", "jobs", ["target_job_id"], ["id"], ondelete="RESTRICT"
        )
        op.create_check_constraint("ck_audit_log_action", "audit_logs", f"action IN ({JOB_ACTIONS})")
        op.create_check_constraint(
            "ck_audit_log_single_target",
            "audit_logs",
            "(target_employee_id IS NOT NULL AND target_job_id IS NULL) OR "
            "(target_employee_id IS NULL AND target_job_id IS NOT NULL)",
        )


def downgrade() -> None:
    bind = op.get_bind()
    job_count = bind.execute(sa.text("SELECT count(*) FROM jobs")).scalar_one()
    job_audit_count = bind.execute(sa.text("SELECT count(*) FROM audit_logs WHERE target_job_id IS NOT NULL")).scalar_one()
    if job_count or job_audit_count:
        raise RuntimeError("Cannot downgrade while job records or audit history exists; refusing to delete data")

    op.drop_index("ix_job_assignments_employee", table_name="job_assignments")
    op.drop_table("job_assignments")
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("audit_logs", recreate="always") as batch:
            batch.drop_constraint("ck_audit_log_action", type_="check")
            batch.drop_constraint("ck_audit_log_single_target", type_="check")
            batch.drop_constraint("fk_audit_logs_target_job_id_jobs", type_="foreignkey")
            batch.drop_column("target_job_id")
            batch.alter_column("target_employee_id", existing_type=sa.Uuid(), nullable=False)
            batch.create_check_constraint(
                "ck_audit_log_employee_action",
                "action IN ('employee.updated', 'employee.role_changed', 'employee.activated', 'employee.deactivated')",
            )
    else:
        op.drop_constraint("ck_audit_log_action", "audit_logs", type_="check")
        op.drop_constraint("ck_audit_log_single_target", "audit_logs", type_="check")
        op.drop_constraint("fk_audit_logs_target_job_id_jobs", "audit_logs", type_="foreignkey")
        op.drop_column("audit_logs", "target_job_id")
        op.alter_column("audit_logs", "target_employee_id", existing_type=sa.Uuid(), nullable=False)
        op.create_check_constraint(
            "ck_audit_log_employee_action",
            "audit_logs",
            "action IN ('employee.updated', 'employee.role_changed', 'employee.activated', 'employee.deactivated')",
        )

    for index in (
        "ix_jobs_status_created",
        "ix_jobs_department",
        "ix_jobs_employment_type",
        "ix_jobs_location",
        "ix_jobs_work_mode",
        "ix_jobs_experience",
        "ix_jobs_deadline",
    ):
        op.drop_index(index, table_name="jobs")
    op.drop_table("jobs")
