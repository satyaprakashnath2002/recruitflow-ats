"""Add Candidate/Application core and audit targets.

Revision ID: 0004_candidate_application_core
Revises: 0003_job_management
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0004_candidate_application_core"
down_revision: str | None = "0003_job_management"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_ACTIONS = (
    "'employee.updated', 'employee.role_changed', 'employee.activated', 'employee.deactivated', "
    "'job.created', 'job.updated', 'job.published', 'job.paused', 'job.closed', 'job.archived', "
    "'job.recruiter_assigned', 'job.recruiter_removed'"
)
ALL_ACTIONS = (
    OLD_ACTIONS
    + ", 'candidate.created', 'candidate.updated', 'application.created', 'application.updated', "
    "'application.stage_changed', 'resume.uploaded', 'resume.replaced', 'resume.downloaded', "
    "'application.note_created', 'application.created_after_deadline'"
)
OLD_TARGET_CHECK = (
    "(target_employee_id IS NOT NULL AND target_job_id IS NULL) OR "
    "(target_employee_id IS NULL AND target_job_id IS NOT NULL)"
)
NEW_TARGET_CHECK = (
    "((CASE WHEN target_employee_id IS NOT NULL THEN 1 ELSE 0 END) + "
    "(CASE WHEN target_job_id IS NOT NULL THEN 1 ELSE 0 END) + "
    "(CASE WHEN target_candidate_id IS NOT NULL THEN 1 ELSE 0 END) + "
    "(CASE WHEN target_application_id IS NOT NULL THEN 1 ELSE 0 END) + "
    "(CASE WHEN target_document_id IS NOT NULL THEN 1 ELSE 0 END)) = 1"
)


def upgrade() -> None:
    op.create_table(
        "candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("phone", sa.String(40), nullable=True),
        sa.Column("location", sa.String(200), nullable=True),
        sa.Column("linkedin_url", sa.String(2048), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(trim(full_name)) > 0", name="ck_candidates_full_name_nonempty"),
        sa.CheckConstraint("length(email) > 2", name="ck_candidates_email_nonempty"),
        sa.CheckConstraint("email = lower(trim(email))", name="ck_candidates_email_canonical"),
        sa.UniqueConstraint("email", name="uq_candidates_email"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "applications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("stage", sa.String(20), server_default="applied", nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("source IN ('public_portal', 'recruiter', 'referral')", name="ck_applications_source"),
        sa.CheckConstraint(
            "stage IN ('applied', 'screening', 'shortlisted', 'interview', 'selected', 'offer', 'hired', 'rejected', 'withdrawn')",
            name="ck_applications_stage",
        ),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_id"], ["employees.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("candidate_id", "job_id", name="uq_applications_candidate_job"),
    )
    op.create_index("ix_applications_job_stage", "applications", ["job_id", "stage"])
    op.create_index("ix_applications_candidate", "applications", ["candidate_id"])
    op.create_index("ix_applications_source", "applications", ["source"])
    op.create_index("ix_applications_created_at", "applications", ["created_at"])

    op.create_table(
        "application_notes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("author_employee_id", sa.Uuid(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["author_employee_id"], ["employees.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_application_notes_application_created", "application_notes", ["application_id", "created_at"])
    op.create_table(
        "stored_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("object_key", sa.String(255), nullable=False),
        sa.Column("purpose", sa.String(20), server_default="resume", nullable=False),
        sa.Column("media_type", sa.String(100), server_default="application/pdf", nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("checksum_sha256", sa.String(64), nullable=False),
        sa.Column("uploaded_by_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("purpose = 'resume'", name="ck_stored_documents_purpose"),
        sa.CheckConstraint("media_type = 'application/pdf'", name="ck_stored_documents_media_type"),
        sa.CheckConstraint("byte_size > 0 AND byte_size <= 10485760", name="ck_stored_documents_size"),
        sa.CheckConstraint("length(checksum_sha256) = 64", name="ck_stored_documents_checksum"),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["uploaded_by_id"], ["employees.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_key", name="uq_stored_documents_object_key"),
    )
    op.create_index("ix_stored_documents_application_created", "stored_documents", ["application_id", "created_at"])

    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("audit_logs", recreate="always") as batch:
            batch.drop_constraint("ck_audit_log_action", type_="check")
            batch.drop_constraint("ck_audit_log_single_target", type_="check")
            batch.alter_column("actor_employee_id", existing_type=sa.Uuid(), nullable=True)
            batch.add_column(sa.Column("target_candidate_id", sa.Uuid(), nullable=True))
            batch.add_column(sa.Column("target_application_id", sa.Uuid(), nullable=True))
            batch.add_column(sa.Column("target_document_id", sa.Uuid(), nullable=True))
            batch.add_column(sa.Column("actor_kind", sa.String(16), server_default="employee", nullable=False))
            batch.create_foreign_key(
                "fk_audit_logs_target_candidate_id_candidates", "candidates", ["target_candidate_id"], ["id"], ondelete="RESTRICT"
            )
            batch.create_foreign_key(
                "fk_audit_logs_target_application_id_applications", "applications", ["target_application_id"], ["id"], ondelete="RESTRICT"
            )
            batch.create_foreign_key(
                "fk_audit_logs_target_document_id_stored_documents", "stored_documents", ["target_document_id"], ["id"], ondelete="RESTRICT"
            )
            batch.create_check_constraint("ck_audit_log_action", f"action IN ({ALL_ACTIONS})")
            batch.create_check_constraint("ck_audit_log_single_target", NEW_TARGET_CHECK)
            batch.create_check_constraint(
                "ck_audit_log_actor",
                "(actor_kind = 'employee' AND actor_employee_id IS NOT NULL) OR "
                "(actor_kind = 'public' AND actor_employee_id IS NULL)",
            )
    else:
        op.drop_constraint("ck_audit_log_action", "audit_logs", type_="check")
        op.drop_constraint("ck_audit_log_single_target", "audit_logs", type_="check")
        op.alter_column("audit_logs", "actor_employee_id", existing_type=sa.Uuid(), nullable=True)
        op.add_column("audit_logs", sa.Column("target_candidate_id", sa.Uuid(), nullable=True))
        op.add_column("audit_logs", sa.Column("target_application_id", sa.Uuid(), nullable=True))
        op.add_column("audit_logs", sa.Column("target_document_id", sa.Uuid(), nullable=True))
        op.add_column("audit_logs", sa.Column("actor_kind", sa.String(16), server_default="employee", nullable=False))
        op.create_foreign_key(
            "fk_audit_logs_target_candidate_id_candidates", "audit_logs", "candidates", ["target_candidate_id"], ["id"], ondelete="RESTRICT"
        )
        op.create_foreign_key(
            "fk_audit_logs_target_application_id_applications", "audit_logs", "applications", ["target_application_id"], ["id"], ondelete="RESTRICT"
        )
        op.create_foreign_key(
            "fk_audit_logs_target_document_id_stored_documents", "audit_logs", "stored_documents", ["target_document_id"], ["id"], ondelete="RESTRICT"
        )
        op.create_check_constraint("ck_audit_log_action", "audit_logs", f"action IN ({ALL_ACTIONS})")
        op.create_check_constraint("ck_audit_log_single_target", "audit_logs", NEW_TARGET_CHECK)
        op.create_check_constraint(
            "ck_audit_log_actor", "audit_logs",
            "(actor_kind = 'employee' AND actor_employee_id IS NOT NULL) OR "
            "(actor_kind = 'public' AND actor_employee_id IS NULL)",
        )

    op.create_index("ix_audit_logs_target_candidate_created", "audit_logs", ["target_candidate_id", "created_at"])
    op.create_index("ix_audit_logs_target_application_created", "audit_logs", ["target_application_id", "created_at"])
    op.create_index("ix_audit_logs_target_document_created", "audit_logs", ["target_document_id", "created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    for table in ("candidates", "applications", "application_notes", "stored_documents"):
        count = bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
        if count:
            raise RuntimeError(f"Cannot downgrade while {table} contains data; refusing to delete Milestone 5 records")
    audit_count = bind.execute(
        sa.text(
            "SELECT count(*) FROM audit_logs WHERE target_candidate_id IS NOT NULL "
            "OR target_application_id IS NOT NULL OR target_document_id IS NOT NULL "
            "OR action LIKE 'candidate.%' OR action LIKE 'application.%' OR action LIKE 'resume.%'"
        )
    ).scalar_one()
    if audit_count:
        raise RuntimeError("Cannot downgrade while Milestone 5 audit history exists; refusing to delete audit records")

    for index in (
        "ix_audit_logs_target_candidate_created",
        "ix_audit_logs_target_application_created",
        "ix_audit_logs_target_document_created",
    ):
        op.drop_index(index, table_name="audit_logs")

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("audit_logs", recreate="always") as batch:
            batch.drop_constraint("ck_audit_log_action", type_="check")
            batch.drop_constraint("ck_audit_log_single_target", type_="check")
            batch.drop_constraint("ck_audit_log_actor", type_="check")
            batch.drop_constraint("fk_audit_logs_target_candidate_id_candidates", type_="foreignkey")
            batch.drop_constraint("fk_audit_logs_target_application_id_applications", type_="foreignkey")
            batch.drop_constraint("fk_audit_logs_target_document_id_stored_documents", type_="foreignkey")
            batch.drop_column("target_candidate_id")
            batch.drop_column("target_application_id")
            batch.drop_column("target_document_id")
            batch.drop_column("actor_kind")
            batch.alter_column("actor_employee_id", existing_type=sa.Uuid(), nullable=False)
            batch.create_check_constraint("ck_audit_log_action", f"action IN ({OLD_ACTIONS})")
            batch.create_check_constraint("ck_audit_log_single_target", OLD_TARGET_CHECK)
    else:
        op.drop_constraint("ck_audit_log_action", "audit_logs", type_="check")
        op.drop_constraint("ck_audit_log_single_target", "audit_logs", type_="check")
        op.drop_constraint("ck_audit_log_actor", "audit_logs", type_="check")
        op.drop_constraint("fk_audit_logs_target_candidate_id_candidates", "audit_logs", type_="foreignkey")
        op.drop_constraint("fk_audit_logs_target_application_id_applications", "audit_logs", type_="foreignkey")
        op.drop_constraint("fk_audit_logs_target_document_id_stored_documents", "audit_logs", type_="foreignkey")
        op.drop_column("audit_logs", "target_candidate_id")
        op.drop_column("audit_logs", "target_application_id")
        op.drop_column("audit_logs", "target_document_id")
        op.drop_column("audit_logs", "actor_kind")
        op.alter_column("audit_logs", "actor_employee_id", existing_type=sa.Uuid(), nullable=False)
        op.create_check_constraint("ck_audit_log_action", "audit_logs", f"action IN ({OLD_ACTIONS})")
        op.create_check_constraint("ck_audit_log_single_target", "audit_logs", OLD_TARGET_CHECK)

    op.drop_index("ix_stored_documents_application_created", table_name="stored_documents")
    op.drop_table("stored_documents")
    op.drop_index("ix_application_notes_application_created", table_name="application_notes")
    op.drop_table("application_notes")
    for index in ("ix_applications_created_at", "ix_applications_source", "ix_applications_candidate", "ix_applications_job_stage"):
        op.drop_index(index, table_name="applications")
    op.drop_table("applications")
    op.drop_table("candidates")
