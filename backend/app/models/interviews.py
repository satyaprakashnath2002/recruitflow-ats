"""Persistence models for the approved Milestone 6 interview data model."""

import enum
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.db.base import Base


class InterviewRoundStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class InterviewRecommendation(str, enum.Enum):
    STRONG_HIRE = "strong_hire"
    HIRE = "hire"
    NO_HIRE = "no_hire"
    STRONG_NO_HIRE = "strong_no_hire"


class InterviewRound(Base):
    __tablename__ = "interview_rounds"
    __table_args__ = (
        CheckConstraint("starts_at < ends_at", name="ck_interview_rounds_time_order"),
        CheckConstraint(
            "status IN ('scheduled', 'completed', 'cancelled')", name="ck_interview_rounds_status"
        ),
        CheckConstraint("length(timezone_name) > 0", name="ck_interview_rounds_timezone_nonempty"),
        Index("ix_interview_rounds_application_created", "application_id", "created_at"),
        Index("ix_interview_rounds_status_starts", "status", "starts_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="RESTRICT"), nullable=False
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timezone_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=InterviewRoundStatus.SCHEDULED.value,
        server_default=InterviewRoundStatus.SCHEDULED.value,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    application: Mapped["Application"] = relationship(back_populates="interview_rounds")
    assignments: Mapped[list["InterviewerAssignment"]] = relationship(back_populates="interview_round")

    @validates("starts_at", "ends_at")
    def require_aware_utc_timestamp(self, _key: str, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Interview timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)

    @validates("timezone_name")
    def require_iana_timezone(self, _key: str, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Interview timezone must be a valid IANA timezone") from exc
        return value


class InterviewerAssignment(Base):
    __tablename__ = "interviewer_assignments"
    __table_args__ = (
        UniqueConstraint("interview_round_id", "interviewer_employee_id", name="uq_interviewer_assignment_round_employee"),
        CheckConstraint(
            "(removed_at IS NULL AND removed_by_id IS NULL) OR "
            "(removed_at IS NOT NULL AND removed_by_id IS NOT NULL)",
            name="ck_interviewer_assignment_removal_pair",
        ),
        Index("ix_interviewer_assignments_employee", "interviewer_employee_id"),
        Index("ix_interviewer_assignments_round", "interview_round_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    interview_round_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("interview_rounds.id", ondelete="RESTRICT"), nullable=False
    )
    interviewer_employee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False
    )
    assigned_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False
    )
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    removed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("employees.id", ondelete="RESTRICT")
    )
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    interview_round: Mapped[InterviewRound] = relationship(back_populates="assignments")
    interviewer: Mapped["Employee"] = relationship(foreign_keys=[interviewer_employee_id])
    assigned_by: Mapped["Employee"] = relationship(foreign_keys=[assigned_by_id])
    removed_by: Mapped["Employee | None"] = relationship(foreign_keys=[removed_by_id])
    feedback: Mapped["InterviewFeedback | None"] = relationship(back_populates="assignment", uselist=False)


class InterviewFeedback(Base):
    __tablename__ = "interview_feedback"
    __table_args__ = (
        UniqueConstraint("interview_round_id", "interviewer_employee_id", name="uq_interview_feedback_round_employee"),
        ForeignKeyConstraint(
            ["interview_round_id", "interviewer_employee_id"],
            ["interviewer_assignments.interview_round_id", "interviewer_assignments.interviewer_employee_id"],
            name="fk_interview_feedback_assigned_interviewer",
            ondelete="RESTRICT",
        ),
        CheckConstraint("rating BETWEEN 1 AND 5", name="ck_interview_feedback_rating_range"),
        CheckConstraint(
            "recommendation IN ('strong_hire', 'hire', 'no_hire', 'strong_no_hire')",
            name="ck_interview_feedback_recommendation",
        ),
        CheckConstraint("length(trim(strengths)) > 0", name="ck_interview_feedback_strengths_nonempty"),
        CheckConstraint(
            "length(trim(areas_for_improvement)) > 0", name="ck_interview_feedback_improvements_nonempty"
        ),
        Index("ix_interview_feedback_round", "interview_round_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    interview_round_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    interviewer_employee_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    rating: Mapped[int] = mapped_column(nullable=False)
    strengths: Mapped[str] = mapped_column(Text, nullable=False)
    areas_for_improvement: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    assignment: Mapped[InterviewerAssignment] = relationship(back_populates="feedback")
    revisions: Mapped[list["InterviewFeedbackRevision"]] = relationship(back_populates="feedback")


class InterviewFeedbackRevision(Base):
    """Immutable snapshot of a prior feedback version, as required by the SDD."""

    __tablename__ = "interview_feedback_revisions"
    __table_args__ = (
        CheckConstraint("rating BETWEEN 1 AND 5", name="ck_interview_feedback_revisions_rating_range"),
        CheckConstraint(
            "recommendation IN ('strong_hire', 'hire', 'no_hire', 'strong_no_hire')",
            name="ck_interview_feedback_revisions_recommendation",
        ),
        Index("ix_interview_feedback_revisions_feedback_created", "feedback_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    feedback_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("interview_feedback.id", ondelete="RESTRICT"), nullable=False
    )
    rating: Mapped[int] = mapped_column(nullable=False)
    strengths: Mapped[str] = mapped_column(Text, nullable=False)
    areas_for_improvement: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation: Mapped[str] = mapped_column(String(20), nullable=False)
    changed_by_employee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    feedback: Mapped[InterviewFeedback] = relationship(back_populates="revisions")
    changed_by: Mapped["Employee"] = relationship()


from app.models.applications import Application  # noqa: E402
from app.models.auth import Employee  # noqa: E402


@event.listens_for(InterviewFeedbackRevision, "before_update")
def prevent_feedback_revision_update(_mapper, _connection, _target) -> None:
    raise ValueError("Interview feedback revisions are append-only")


@event.listens_for(InterviewFeedbackRevision, "before_delete")
def prevent_feedback_revision_delete(_mapper, _connection, _target) -> None:
    raise ValueError("Interview feedback revisions are append-only")


@event.listens_for(InterviewRound, "before_delete")
def prevent_interview_round_delete(_mapper, _connection, _target) -> None:
    raise ValueError("Interview history is retained")


@event.listens_for(InterviewerAssignment, "before_delete")
def prevent_interviewer_assignment_delete(_mapper, _connection, _target) -> None:
    raise ValueError("Interview assignment history is retained")


@event.listens_for(InterviewFeedback, "before_delete")
def prevent_interview_feedback_delete(_mapper, _connection, _target) -> None:
    raise ValueError("Interview feedback history is retained")
