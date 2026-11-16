"""Add approved employee-managed Offer workflow and private letter revisions.

Revision ID: 0010_offer_management
Revises: 0009_in_app_notifications
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0010_offer_management"
down_revision: str | None = "0009_in_app_notifications"
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
    "'interview.feedback_created', 'interview.feedback_modified', 'offer.created', 'offer.revised', "
    "'offer.submitted', 'offer.approved', 'offer.sent', 'offer.accepted', 'offer.rejected', "
    "'offer.withdrawn', 'offer.expired', 'offer.letter_downloaded'"
)
_OLD_ACTIONS = _ACTIONS[:_ACTIONS.index(", 'offer.created'")]
_OFFER_EVENT_TYPES = "'offer_sent', 'offer_expiry_3d', 'offer_expiry_1d'"
_INTERVIEW_EVENT_TYPES = (
    "'interview_scheduled', 'interview_rescheduled', 'interview_cancelled', "
    "'interview_completed', 'interview_reminder_24h', 'interview_reminder_1h'"
)


def _create_revision_immutability_triggers() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "CREATE OR REPLACE FUNCTION recruitflow_reject_offer_revision_mutation() "
            "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
            "RAISE EXCEPTION 'offer revisions are immutable'; END; $$"
        )
        op.execute(
            "CREATE TRIGGER trg_offer_revisions_immutable BEFORE UPDATE OR DELETE ON offer_revisions "
            "FOR EACH ROW EXECUTE FUNCTION recruitflow_reject_offer_revision_mutation()"
        )
    elif bind.dialect.name == "sqlite":
        op.execute(
            "CREATE TRIGGER trg_offer_revisions_no_update BEFORE UPDATE ON offer_revisions "
            "BEGIN SELECT RAISE(ABORT, 'offer revisions are immutable'); END"
        )
        op.execute(
            "CREATE TRIGGER trg_offer_revisions_no_delete BEFORE DELETE ON offer_revisions "
            "BEGIN SELECT RAISE(ABORT, 'offer revisions are retained'); END"
        )


def _drop_revision_immutability_triggers() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS trg_offer_revisions_immutable ON offer_revisions")
        op.execute("DROP FUNCTION IF EXISTS recruitflow_reject_offer_revision_mutation()")
    elif bind.dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_offer_revisions_no_update")
        op.execute("DROP TRIGGER IF EXISTS trg_offer_revisions_no_delete")


def upgrade() -> None:
    op.create_table(
        "offers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_employee_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("current_revision_number", sa.Integer(), nullable=False),
        sa.Column("approved_revision_number", sa.Integer(), nullable=True),
        sa.Column("approved_by_employee_id", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("capability_token_hash", sa.String(length=64), nullable=True),
        sa.Column("capability_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("capability_revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'pending_approval', 'approved', 'sent', 'accepted', 'rejected', 'withdrawn', 'expired')",
            name="ck_offers_status",
        ),
        sa.CheckConstraint(
            "(approved_by_employee_id IS NULL AND approved_at IS NULL AND approved_revision_number IS NULL) OR "
            "(approved_by_employee_id IS NOT NULL AND approved_at IS NOT NULL AND approved_revision_number IS NOT NULL)",
            name="ck_offers_approval_fields_pair",
        ),
        sa.CheckConstraint(
            "(status IN ('draft', 'pending_approval', 'approved') AND sent_at IS NULL) OR "
            "(status IN ('sent', 'accepted', 'rejected', 'expired') AND sent_at IS NOT NULL) OR status = 'withdrawn'",
            name="ck_offers_sent_fields_pair",
        ),
        sa.CheckConstraint(
            "(status != 'withdrawn' AND withdrawn_at IS NULL) OR (status = 'withdrawn' AND withdrawn_at IS NOT NULL)",
            name="ck_offers_withdrawn_fields_pair",
        ),
        sa.CheckConstraint(
            "(status != 'expired' AND expired_at IS NULL) OR (status = 'expired' AND expired_at IS NOT NULL)",
            name="ck_offers_expired_fields_pair",
        ),
        sa.CheckConstraint(
            "(status IN ('accepted', 'rejected') AND responded_at IS NOT NULL) OR "
            "(status NOT IN ('accepted', 'rejected') AND responded_at IS NULL)",
            name="ck_offers_response_fields_pair",
        ),
        sa.CheckConstraint(
            "(capability_token_hash IS NULL AND capability_expires_at IS NULL) OR "
            "(capability_token_hash IS NOT NULL AND capability_expires_at IS NOT NULL)",
            name="ck_offers_capability_fields_pair",
        ),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_employee_id"], ["employees.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["approved_by_employee_id"], ["employees.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("capability_token_hash", name="uq_offers_capability_token_hash"),
    )
    op.create_index(
        "uq_offers_one_active_per_application",
        "offers",
        ["application_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('draft', 'pending_approval', 'approved', 'sent')"),
        sqlite_where=sa.text("status IN ('draft', 'pending_approval', 'approved', 'sent')"),
    )
    op.create_index("ix_offers_status_updated", "offers", ["status", "updated_at"])
    op.create_table(
        "offer_revisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("offer_id", sa.Uuid(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("compensation_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("compensation_currency", sa.String(length=3), nullable=False),
        sa.Column("compensation_basis", sa.String(length=80), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("letter_object_key", sa.String(length=255), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_by_employee_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("revision_number > 0", name="ck_offer_revisions_number_positive"),
        sa.CheckConstraint("compensation_amount > 0", name="ck_offer_revisions_amount_positive"),
        sa.CheckConstraint(
            "length(compensation_currency) = 3 AND compensation_currency = upper(compensation_currency)",
            name="ck_offer_revisions_currency",
        ),
        sa.CheckConstraint("length(trim(compensation_basis)) > 0", name="ck_offer_revisions_basis_nonempty"),
        sa.CheckConstraint("byte_size > 0 AND byte_size <= 10485760", name="ck_offer_revisions_letter_size"),
        sa.CheckConstraint("length(checksum_sha256) = 64", name="ck_offer_revisions_checksum"),
        sa.ForeignKeyConstraint(["offer_id"], ["offers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_employee_id"], ["employees.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("offer_id", "revision_number", name="uq_offer_revisions_offer_number"),
        sa.UniqueConstraint("letter_object_key", name="uq_offer_revisions_object_key"),
    )
    op.create_index("ix_offer_revisions_offer_created", "offer_revisions", ["offer_id", "created_at"])
    op.create_table(
        "scheduled_offer_reminders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("offer_id", sa.Uuid(), nullable=False),
        sa.Column("offer_revision_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_employee_id", sa.Uuid(), nullable=False),
        sa.Column("offset_days", sa.Integer(), nullable=False),
        sa.Column("expires_at_snapshot", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.CheckConstraint("offset_days IN (1, 3)", name="ck_scheduled_offer_reminders_offset"),
        sa.CheckConstraint("state IN ('pending', 'delivered', 'superseded')", name="ck_scheduled_offer_reminders_state"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_scheduled_offer_reminders_attempts"),
        sa.ForeignKeyConstraint(["offer_id"], ["offers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["offer_revision_id"], ["offer_revisions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["recipient_employee_id"], ["employees.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_scheduled_offer_reminders_idempotency_key"),
    )
    op.create_index("ix_scheduled_offer_reminders_due", "scheduled_offer_reminders", ["state", "scheduled_at"])

    with op.batch_alter_table("audit_logs") as batch:
        batch.drop_constraint("ck_audit_log_action", type_="check")
        batch.drop_constraint("ck_audit_log_single_target", type_="check")
        batch.add_column(sa.Column("target_offer_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_audit_logs_target_offer_id_offers", "offers", ["target_offer_id"], ["id"], ondelete="RESTRICT"
        )
        batch.create_check_constraint("ck_audit_log_action", f"action IN ({_ACTIONS})")
        batch.create_check_constraint(
            "ck_audit_log_single_target",
            "((CASE WHEN target_employee_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN target_job_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN target_candidate_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN target_application_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN target_document_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN target_offer_id IS NOT NULL THEN 1 ELSE 0 END)) = 1",
        )
    op.create_index("ix_audit_logs_target_offer_created", "audit_logs", ["target_offer_id", "created_at"])

    with op.batch_alter_table("notifications") as batch:
        batch.drop_constraint("ck_notifications_event_type", type_="check")
        batch.alter_column("resource_id", existing_type=sa.Uuid(), nullable=True)
        batch.add_column(sa.Column("offer_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key("fk_notifications_offer_id_offers", "offers", ["offer_id"], ["id"], ondelete="RESTRICT")
        batch.create_check_constraint("ck_notifications_event_type", f"event_type IN ({_INTERVIEW_EVENT_TYPES}, {_OFFER_EVENT_TYPES})")
        batch.create_check_constraint(
            "ck_notifications_resource",
            "(event_type LIKE 'interview_%' AND resource_type = 'interview_round' "
            "AND resource_id IS NOT NULL AND offer_id IS NULL) OR "
            "(event_type LIKE 'offer_%' AND resource_type = 'offer' "
            "AND resource_id IS NULL AND offer_id IS NOT NULL)",
        )

    _create_revision_immutability_triggers()


def downgrade() -> None:
    bind = op.get_bind()
    counts = {
        "offers": bind.execute(sa.text("SELECT count(*) FROM offers")).scalar_one(),
        "revisions": bind.execute(sa.text("SELECT count(*) FROM offer_revisions")).scalar_one(),
        "reminders": bind.execute(sa.text("SELECT count(*) FROM scheduled_offer_reminders")).scalar_one(),
        "notifications": bind.execute(sa.text("SELECT count(*) FROM notifications WHERE offer_id IS NOT NULL")).scalar_one(),
        "audit": bind.execute(sa.text("SELECT count(*) FROM audit_logs WHERE target_offer_id IS NOT NULL")).scalar_one(),
    }
    if any(counts.values()):
        raise RuntimeError("Cannot downgrade while Offer, Offer revision, reminder, notification, or audit history exists")

    _drop_revision_immutability_triggers()
    op.drop_index("ix_scheduled_offer_reminders_due", table_name="scheduled_offer_reminders")
    op.drop_table("scheduled_offer_reminders")
    with op.batch_alter_table("notifications") as batch:
        batch.drop_constraint("ck_notifications_resource", type_="check")
        batch.drop_constraint("ck_notifications_event_type", type_="check")
        batch.drop_constraint("fk_notifications_offer_id_offers", type_="foreignkey")
        batch.drop_column("offer_id")
        batch.alter_column("resource_id", existing_type=sa.Uuid(), nullable=False)
        batch.create_check_constraint("ck_notifications_event_type", f"event_type IN ({_INTERVIEW_EVENT_TYPES})")
    op.drop_index("ix_audit_logs_target_offer_created", table_name="audit_logs")
    with op.batch_alter_table("audit_logs") as batch:
        batch.drop_constraint("ck_audit_log_action", type_="check")
        batch.drop_constraint("ck_audit_log_single_target", type_="check")
        batch.drop_constraint("fk_audit_logs_target_offer_id_offers", type_="foreignkey")
        batch.drop_column("target_offer_id")
        batch.create_check_constraint("ck_audit_log_action", f"action IN ({_OLD_ACTIONS})")
        batch.create_check_constraint(
            "ck_audit_log_single_target",
            "((CASE WHEN target_employee_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN target_job_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN target_candidate_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN target_application_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN target_document_id IS NOT NULL THEN 1 ELSE 0 END)) = 1",
        )
    op.drop_index("ix_offer_revisions_offer_created", table_name="offer_revisions")
    op.drop_table("offer_revisions")
    op.drop_index("ix_offers_status_updated", table_name="offers")
    op.drop_index("uq_offers_one_active_per_application", table_name="offers")
    op.drop_table("offers")
