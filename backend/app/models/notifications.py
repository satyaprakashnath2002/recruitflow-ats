"""Persistent employee in-app notifications and interview reminder jobs."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('interview_scheduled', 'interview_rescheduled', 'interview_cancelled', "
            "'interview_completed', 'interview_reminder_24h', 'interview_reminder_1h', "
            "'offer_sent', 'offer_expiry_3d', 'offer_expiry_1d')",
            name="ck_notifications_event_type",
        ),
        CheckConstraint(
            "(event_type LIKE 'interview_%' AND resource_type = 'interview_round' "
            "AND resource_id IS NOT NULL AND offer_id IS NULL) OR "
            "(event_type LIKE 'offer_%' AND resource_type = 'offer' "
            "AND resource_id IS NULL AND offer_id IS NOT NULL)",
            name="ck_notifications_resource",
        ),
        UniqueConstraint("idempotency_key", name="uq_notifications_idempotency_key"),
        Index("ix_notifications_recipient_created", "recipient_employee_id", "created_at"),
        Index("ix_notifications_recipient_read_created", "recipient_employee_id", "read_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    recipient_employee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    body: Mapped[str] = mapped_column(String(500), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(40), nullable=False)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("interview_rounds.id", ondelete="RESTRICT"), nullable=True
    )
    offer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("offers.id", ondelete="RESTRICT"))
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ScheduledReminder(Base):
    __tablename__ = "scheduled_reminders"
    __table_args__ = (
        CheckConstraint("offset_minutes IN (60, 1440)", name="ck_scheduled_reminders_offset"),
        CheckConstraint("state IN ('pending', 'delivered', 'superseded')", name="ck_scheduled_reminders_state"),
        CheckConstraint("attempt_count >= 0", name="ck_scheduled_reminders_attempts"),
        UniqueConstraint("idempotency_key", name="uq_scheduled_reminders_idempotency_key"),
        Index("ix_scheduled_reminders_due", "state", "scheduled_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    interview_round_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("interview_rounds.id", ondelete="RESTRICT"), nullable=False
    )
    interviewer_employee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False
    )
    offset_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    starts_at_snapshot: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class ScheduledOfferReminder(Base):
    __tablename__ = "scheduled_offer_reminders"
    __table_args__ = (
        CheckConstraint("offset_days IN (1, 3)", name="ck_scheduled_offer_reminders_offset"),
        CheckConstraint("state IN ('pending', 'delivered', 'superseded')", name="ck_scheduled_offer_reminders_state"),
        CheckConstraint("attempt_count >= 0", name="ck_scheduled_offer_reminders_attempts"),
        UniqueConstraint("idempotency_key", name="uq_scheduled_offer_reminders_idempotency_key"),
        Index("ix_scheduled_offer_reminders_due", "state", "scheduled_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    offer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("offers.id", ondelete="RESTRICT"), nullable=False)
    offer_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("offer_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    recipient_employee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False
    )
    offset_days: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at_snapshot: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

