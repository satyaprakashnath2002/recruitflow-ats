"""Offer persistence for the employee-managed MVP offer workflow."""

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    event,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class OfferStatus(str, enum.Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    SENT = "sent"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    EXPIRED = "expired"


class Offer(Base):
    __tablename__ = "offers"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'pending_approval', 'approved', 'sent', 'accepted', 'rejected', 'withdrawn', 'expired')",
            name="ck_offers_status",
        ),
        CheckConstraint(
            "(approved_by_employee_id IS NULL AND approved_at IS NULL AND approved_revision_number IS NULL) OR "
            "(approved_by_employee_id IS NOT NULL AND approved_at IS NOT NULL AND approved_revision_number IS NOT NULL)",
            name="ck_offers_approval_fields_pair",
        ),
        CheckConstraint(
            "(status IN ('draft', 'pending_approval', 'approved') AND sent_at IS NULL) OR "
            "(status IN ('sent', 'accepted', 'rejected', 'expired') AND sent_at IS NOT NULL) OR "
            "status = 'withdrawn'",
            name="ck_offers_sent_fields_pair",
        ),
        CheckConstraint(
            "(status != 'withdrawn' AND withdrawn_at IS NULL) OR (status = 'withdrawn' AND withdrawn_at IS NOT NULL)",
            name="ck_offers_withdrawn_fields_pair",
        ),
        CheckConstraint(
            "(status != 'expired' AND expired_at IS NULL) OR (status = 'expired' AND expired_at IS NOT NULL)",
            name="ck_offers_expired_fields_pair",
        ),
        CheckConstraint(
            "(status IN ('accepted', 'rejected') AND responded_at IS NOT NULL) OR "
            "(status NOT IN ('accepted', 'rejected') AND responded_at IS NULL)",
            name="ck_offers_response_fields_pair",
        ),
        CheckConstraint(
            "(capability_token_hash IS NULL AND capability_expires_at IS NULL) OR "
            "(capability_token_hash IS NOT NULL AND capability_expires_at IS NOT NULL)",
            name="ck_offers_capability_fields_pair",
        ),
        UniqueConstraint("capability_token_hash", name="uq_offers_capability_token_hash"),
        Index(
            "uq_offers_one_active_per_application",
            "application_id",
            unique=True,
            postgresql_where=text("status IN ('draft', 'pending_approval', 'approved', 'sent')"),
            sqlite_where=text("status IN ('draft', 'pending_approval', 'approved', 'sent')"),
        ),
        Index("ix_offers_status_updated", "status", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id", ondelete="RESTRICT"), nullable=False)
    created_by_employee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default=OfferStatus.DRAFT.value)
    current_revision_number: Mapped[int] = mapped_column(nullable=False, default=1)
    approved_revision_number: Mapped[int | None] = mapped_column()
    approved_by_employee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    capability_token_hash: Mapped[str | None] = mapped_column(String(64))
    capability_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    capability_revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    application: Mapped["Application"] = relationship(back_populates="offers")
    created_by: Mapped["Employee"] = relationship(foreign_keys=[created_by_employee_id])
    approved_by: Mapped["Employee | None"] = relationship(foreign_keys=[approved_by_employee_id])
    revisions: Mapped[list["OfferRevision"]] = relationship(back_populates="offer", order_by="OfferRevision.revision_number")


class OfferRevision(Base):
    __tablename__ = "offer_revisions"
    __table_args__ = (
        CheckConstraint("revision_number > 0", name="ck_offer_revisions_number_positive"),
        CheckConstraint("compensation_amount > 0", name="ck_offer_revisions_amount_positive"),
        CheckConstraint(
            "length(compensation_currency) = 3 AND compensation_currency = upper(compensation_currency)",
            name="ck_offer_revisions_currency",
        ),
        CheckConstraint("length(trim(compensation_basis)) > 0", name="ck_offer_revisions_basis_nonempty"),
        CheckConstraint("byte_size > 0 AND byte_size <= 10485760", name="ck_offer_revisions_letter_size"),
        CheckConstraint("length(checksum_sha256) = 64", name="ck_offer_revisions_checksum"),
        UniqueConstraint("offer_id", "revision_number", name="uq_offer_revisions_offer_number"),
        UniqueConstraint("letter_object_key", name="uq_offer_revisions_object_key"),
        Index("ix_offer_revisions_offer_created", "offer_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    offer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("offers.id", ondelete="RESTRICT"), nullable=False)
    revision_number: Mapped[int] = mapped_column(nullable=False)
    compensation_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    compensation_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    compensation_basis: Mapped[str] = mapped_column(String(80), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    letter_object_key: Mapped[str] = mapped_column(String(255), nullable=False)
    byte_size: Mapped[int] = mapped_column(nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_employee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    offer: Mapped[Offer] = relationship(back_populates="revisions")
    created_by: Mapped["Employee"] = relationship(foreign_keys=[created_by_employee_id])


@event.listens_for(OfferRevision, "before_update")
def prevent_offer_revision_update(_mapper, _connection, _target) -> None:
    raise ValueError("Offer revisions are immutable")


@event.listens_for(OfferRevision, "before_delete")
def prevent_offer_revision_delete(_mapper, _connection, _target) -> None:
    raise ValueError("Offer revisions are retained")


from app.models.applications import Application  # noqa: E402
from app.models.auth import Employee  # noqa: E402
