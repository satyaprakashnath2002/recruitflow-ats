"""Prevent changes to closed interview round schedules.

Revision ID: 0007_terminal_interview_immutability
Revises: 0006_interview_scheduling_audit
"""

from typing import Sequence

from alembic import op


revision: str = "0007_terminal_immutable"
down_revision: str | None = "0006_interview_scheduling_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _install_postgresql_guard(*, terminal_immutable: bool) -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_interview_rounds_guard ON interview_rounds")
    if terminal_immutable:
        terminal_check = """
                IF OLD.status IN ('completed', 'cancelled') THEN
                    RAISE EXCEPTION 'terminal interview rounds are immutable' USING ERRCODE = 'restrict_violation';
                END IF;
"""
        trigger_events = "BEFORE UPDATE OR DELETE"
    else:
        terminal_check = ""
        trigger_events = "BEFORE UPDATE OF status OR DELETE"
    op.execute(
        f"""CREATE OR REPLACE FUNCTION recruitflow_guard_interview_round() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'interview history is retained' USING ERRCODE = 'restrict_violation';
            END IF;
            {terminal_check}
            IF NOT (OLD.status = NEW.status OR
                (OLD.status = 'scheduled' AND NEW.status IN ('completed', 'cancelled'))) THEN
                RAISE EXCEPTION 'invalid interview round lifecycle transition' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END; $$ LANGUAGE plpgsql"""
    )
    op.execute(
        f"CREATE TRIGGER trg_interview_rounds_guard {trigger_events} ON interview_rounds "
        "FOR EACH ROW EXECUTE FUNCTION recruitflow_guard_interview_round()"
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        _install_postgresql_guard(terminal_immutable=True)
    elif bind.dialect.name == "sqlite":
        op.execute(
            """CREATE TRIGGER trg_interview_round_terminal_immutable
            BEFORE UPDATE ON interview_rounds
            WHEN OLD.status IN ('completed', 'cancelled')
            BEGIN SELECT RAISE(ABORT, 'terminal interview rounds are immutable'); END"""
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        _install_postgresql_guard(terminal_immutable=False)
    elif bind.dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_interview_round_terminal_immutable")
