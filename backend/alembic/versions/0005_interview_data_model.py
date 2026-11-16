"""Add the approved Milestone 6 interview data model.

Revision ID: 0005_interview_data_model
Revises: 0004_candidate_application_core
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0005_interview_data_model"
down_revision: str | None = "0004_candidate_application_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "interview_rounds",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone_name", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="scheduled", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("starts_at < ends_at", name="ck_interview_rounds_time_order"),
        sa.CheckConstraint(
            "status IN ('scheduled', 'completed', 'cancelled')", name="ck_interview_rounds_status"
        ),
        sa.CheckConstraint("length(timezone_name) > 0", name="ck_interview_rounds_timezone_nonempty"),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_interview_rounds_application_created", "interview_rounds", ["application_id", "created_at"]
    )
    op.create_index("ix_interview_rounds_status_starts", "interview_rounds", ["status", "starts_at"])

    op.create_table(
        "interviewer_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("interview_round_id", sa.Uuid(), nullable=False),
        sa.Column("interviewer_employee_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_by_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("removed_by_id", sa.Uuid(), nullable=True),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(removed_at IS NULL AND removed_by_id IS NULL) OR "
            "(removed_at IS NOT NULL AND removed_by_id IS NOT NULL)",
            name="ck_interviewer_assignment_removal_pair",
        ),
        sa.ForeignKeyConstraint(["interview_round_id"], ["interview_rounds.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["interviewer_employee_id"], ["employees.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assigned_by_id"], ["employees.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["removed_by_id"], ["employees.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "interview_round_id", "interviewer_employee_id", name="uq_interviewer_assignment_round_employee"
        ),
    )
    op.create_index("ix_interviewer_assignments_employee", "interviewer_assignments", ["interviewer_employee_id"])
    op.create_index("ix_interviewer_assignments_round", "interviewer_assignments", ["interview_round_id"])

    op.create_table(
        "interview_feedback",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("interview_round_id", sa.Uuid(), nullable=False),
        sa.Column("interviewer_employee_id", sa.Uuid(), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("strengths", sa.Text(), nullable=False),
        sa.Column("areas_for_improvement", sa.Text(), nullable=False),
        sa.Column("recommendation", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("rating BETWEEN 1 AND 5", name="ck_interview_feedback_rating_range"),
        sa.CheckConstraint(
            "recommendation IN ('strong_hire', 'hire', 'no_hire', 'strong_no_hire')",
            name="ck_interview_feedback_recommendation",
        ),
        sa.CheckConstraint("length(trim(strengths)) > 0", name="ck_interview_feedback_strengths_nonempty"),
        sa.CheckConstraint(
            "length(trim(areas_for_improvement)) > 0", name="ck_interview_feedback_improvements_nonempty"
        ),
        sa.ForeignKeyConstraint(
            ["interview_round_id", "interviewer_employee_id"],
            ["interviewer_assignments.interview_round_id", "interviewer_assignments.interviewer_employee_id"],
            name="fk_interview_feedback_assigned_interviewer",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "interview_round_id", "interviewer_employee_id", name="uq_interview_feedback_round_employee"
        ),
    )
    op.create_index("ix_interview_feedback_round", "interview_feedback", ["interview_round_id"])

    op.create_table(
        "interview_feedback_revisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("feedback_id", sa.Uuid(), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("strengths", sa.Text(), nullable=False),
        sa.Column("areas_for_improvement", sa.Text(), nullable=False),
        sa.Column("recommendation", sa.String(length=20), nullable=False),
        sa.Column("changed_by_employee_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("rating BETWEEN 1 AND 5", name="ck_interview_feedback_revisions_rating_range"),
        sa.CheckConstraint(
            "recommendation IN ('strong_hire', 'hire', 'no_hire', 'strong_no_hire')",
            name="ck_interview_feedback_revisions_recommendation",
        ),
        sa.ForeignKeyConstraint(["feedback_id"], ["interview_feedback.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["changed_by_employee_id"], ["employees.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_interview_feedback_revisions_feedback_created",
        "interview_feedback_revisions",
        ["feedback_id", "created_at"],
    )

    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.execute(
            """CREATE TRIGGER trg_interview_rounds_lifecycle_guard
            BEFORE UPDATE OF status ON interview_rounds
            WHEN NOT (OLD.status = NEW.status OR
                (OLD.status = 'scheduled' AND NEW.status IN ('completed', 'cancelled')))
            BEGIN SELECT RAISE(ABORT, 'invalid interview round lifecycle transition'); END"""
        )
        op.execute(
            """CREATE TRIGGER trg_interview_rounds_no_delete
            BEFORE DELETE ON interview_rounds
            BEGIN SELECT RAISE(ABORT, 'interview history is retained'); END"""
        )
        op.execute(
            """CREATE TRIGGER trg_interviewer_assignments_guard_insert
            BEFORE INSERT ON interviewer_assignments
            BEGIN
                SELECT CASE WHEN NOT EXISTS (
                    SELECT 1 FROM employees
                    WHERE id = NEW.interviewer_employee_id AND role = 'interviewer' AND status = 'active'
                ) THEN RAISE(ABORT, 'assignment requires an active Interviewer employee') END;
                SELECT CASE WHEN (SELECT status FROM interview_rounds WHERE id = NEW.interview_round_id) <> 'scheduled'
                    THEN RAISE(ABORT, 'assignments are locked after interview round closure') END;
            END"""
        )
        op.execute(
            """CREATE TRIGGER trg_interviewer_assignments_guard_update
            BEFORE UPDATE ON interviewer_assignments
            BEGIN
                SELECT CASE WHEN (SELECT status FROM interview_rounds WHERE id = NEW.interview_round_id) <> 'scheduled'
                    THEN RAISE(ABORT, 'assignments are locked after interview round closure') END;
                SELECT CASE WHEN NEW.interviewer_employee_id <> OLD.interviewer_employee_id AND NOT EXISTS (
                    SELECT 1 FROM employees
                    WHERE id = NEW.interviewer_employee_id AND role = 'interviewer' AND status = 'active'
                ) THEN RAISE(ABORT, 'assignment requires an active Interviewer employee') END;
            END"""
        )
        op.execute(
            """CREATE TRIGGER trg_interviewer_assignments_no_delete
            BEFORE DELETE ON interviewer_assignments
            BEGIN SELECT RAISE(ABORT, 'interview assignment history is retained'); END"""
        )
        op.execute(
            """CREATE TRIGGER trg_interview_feedback_no_delete
            BEFORE DELETE ON interview_feedback
            BEGIN SELECT RAISE(ABORT, 'interview feedback history is retained'); END"""
        )
        op.execute(
            """CREATE TRIGGER trg_interview_feedback_revisions_immutable_update
            BEFORE UPDATE ON interview_feedback_revisions
            BEGIN SELECT RAISE(ABORT, 'interview feedback revisions are append-only'); END"""
        )
        op.execute(
            """CREATE TRIGGER trg_interview_feedback_revisions_immutable_delete
            BEFORE DELETE ON interview_feedback_revisions
            BEGIN SELECT RAISE(ABORT, 'interview feedback revisions are append-only'); END"""
        )
        op.execute(
            """CREATE TRIGGER trg_employee_interview_deactivation_guard
            BEFORE UPDATE OF status ON employees
            WHEN NEW.status = 'disabled' AND OLD.status <> 'disabled'
            BEGIN
                SELECT CASE WHEN EXISTS (
                    SELECT 1 FROM interviewer_assignments AS assignment
                    JOIN interview_rounds AS round ON round.id = assignment.interview_round_id
                    WHERE assignment.interviewer_employee_id = OLD.id
                      AND assignment.removed_at IS NULL
                      AND round.status = 'scheduled'
                      AND julianday(round.ends_at) > julianday('now')
                ) THEN RAISE(ABORT, 'employee has future Scheduled interview assignments') END;
            END"""
        )
    elif bind.dialect.name == "postgresql":
        op.execute(
            """CREATE FUNCTION recruitflow_guard_interview_round() RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'interview history is retained' USING ERRCODE = 'restrict_violation';
                END IF;
                IF NOT (OLD.status = NEW.status OR
                    (OLD.status = 'scheduled' AND NEW.status IN ('completed', 'cancelled'))) THEN
                    RAISE EXCEPTION 'invalid interview round lifecycle transition' USING ERRCODE = 'check_violation';
                END IF;
                RETURN NEW;
            END; $$ LANGUAGE plpgsql"""
        )
        op.execute(
            "CREATE TRIGGER trg_interview_rounds_guard BEFORE UPDATE OF status OR DELETE ON interview_rounds "
            "FOR EACH ROW EXECUTE FUNCTION recruitflow_guard_interview_round()"
        )
        op.execute(
            """CREATE FUNCTION recruitflow_guard_interviewer_assignment() RETURNS trigger AS $$
            DECLARE round_status text;
            DECLARE interviewer_role text;
            DECLARE interviewer_status text;
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'interview assignment history is retained' USING ERRCODE = 'restrict_violation';
                END IF;
                SELECT status INTO round_status FROM interview_rounds WHERE id = NEW.interview_round_id;
                IF round_status IS DISTINCT FROM 'scheduled' THEN
                    RAISE EXCEPTION 'assignments are locked after interview round closure' USING ERRCODE = 'check_violation';
                END IF;
                IF TG_OP = 'INSERT' OR NEW.interviewer_employee_id <> OLD.interviewer_employee_id THEN
                    SELECT role, status INTO interviewer_role, interviewer_status
                    FROM employees WHERE id = NEW.interviewer_employee_id;
                    IF interviewer_role IS DISTINCT FROM 'interviewer' OR interviewer_status IS DISTINCT FROM 'active' THEN
                        RAISE EXCEPTION 'assignment requires an active Interviewer employee' USING ERRCODE = 'check_violation';
                    END IF;
                END IF;
                RETURN NEW;
            END; $$ LANGUAGE plpgsql"""
        )
        op.execute(
            "CREATE TRIGGER trg_interviewer_assignments_guard BEFORE INSERT OR UPDATE OR DELETE ON interviewer_assignments "
            "FOR EACH ROW EXECUTE FUNCTION recruitflow_guard_interviewer_assignment()"
        )
        op.execute(
            """CREATE FUNCTION recruitflow_guard_interview_feedback_delete() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'interview feedback history is retained' USING ERRCODE = 'restrict_violation';
            END; $$ LANGUAGE plpgsql"""
        )
        op.execute(
            "CREATE TRIGGER trg_interview_feedback_no_delete BEFORE DELETE ON interview_feedback "
            "FOR EACH ROW EXECUTE FUNCTION recruitflow_guard_interview_feedback_delete()"
        )
        op.execute(
            """CREATE FUNCTION recruitflow_guard_feedback_revision() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'interview feedback revisions are append-only' USING ERRCODE = 'restrict_violation';
            END; $$ LANGUAGE plpgsql"""
        )
        op.execute(
            "CREATE TRIGGER trg_interview_feedback_revisions_immutable BEFORE UPDATE OR DELETE ON interview_feedback_revisions "
            "FOR EACH ROW EXECUTE FUNCTION recruitflow_guard_feedback_revision()"
        )
        op.execute(
            """CREATE FUNCTION recruitflow_guard_employee_interview_deactivation() RETURNS trigger AS $$
            BEGIN
                IF NEW.status = 'disabled' AND OLD.status <> 'disabled' AND EXISTS (
                    SELECT 1 FROM interviewer_assignments AS assignment
                    JOIN interview_rounds AS round ON round.id = assignment.interview_round_id
                    WHERE assignment.interviewer_employee_id = OLD.id
                      AND assignment.removed_at IS NULL
                      AND round.status = 'scheduled'
                      AND round.ends_at > CURRENT_TIMESTAMP
                ) THEN
                    RAISE EXCEPTION 'employee has future Scheduled interview assignments' USING ERRCODE = 'check_violation';
                END IF;
                RETURN NEW;
            END; $$ LANGUAGE plpgsql"""
        )
        op.execute(
            "CREATE TRIGGER trg_employee_interview_deactivation_guard BEFORE UPDATE OF status ON employees "
            "FOR EACH ROW EXECUTE FUNCTION recruitflow_guard_employee_interview_deactivation()"
        )


def downgrade() -> None:
    bind = op.get_bind()
    for table in (
        "interview_feedback_revisions",
        "interview_feedback",
        "interviewer_assignments",
        "interview_rounds",
    ):
        count = bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
        if count:
            raise RuntimeError(
                f"Cannot downgrade while {table} contains data; refusing to delete Milestone 6 interview history"
            )

    if bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER trg_employee_interview_deactivation_guard ON employees")
        op.execute("DROP FUNCTION recruitflow_guard_employee_interview_deactivation()")
        op.execute("DROP TRIGGER trg_interview_feedback_revisions_immutable ON interview_feedback_revisions")
        op.execute("DROP FUNCTION recruitflow_guard_feedback_revision()")
        op.execute("DROP TRIGGER trg_interview_feedback_no_delete ON interview_feedback")
        op.execute("DROP FUNCTION recruitflow_guard_interview_feedback_delete()")
        op.execute("DROP TRIGGER trg_interviewer_assignments_guard ON interviewer_assignments")
        op.execute("DROP FUNCTION recruitflow_guard_interviewer_assignment()")
        op.execute("DROP TRIGGER trg_interview_rounds_guard ON interview_rounds")
        op.execute("DROP FUNCTION recruitflow_guard_interview_round()")
    else:
        op.execute("DROP TRIGGER trg_employee_interview_deactivation_guard")

    op.drop_index("ix_interview_feedback_revisions_feedback_created", table_name="interview_feedback_revisions")
    op.drop_table("interview_feedback_revisions")
    op.drop_index("ix_interview_feedback_round", table_name="interview_feedback")
    op.drop_table("interview_feedback")
    op.drop_index("ix_interviewer_assignments_round", table_name="interviewer_assignments")
    op.drop_index("ix_interviewer_assignments_employee", table_name="interviewer_assignments")
    op.drop_table("interviewer_assignments")
    op.drop_index("ix_interview_rounds_status_starts", table_name="interview_rounds")
    op.drop_index("ix_interview_rounds_application_created", table_name="interview_rounds")
    op.drop_table("interview_rounds")
