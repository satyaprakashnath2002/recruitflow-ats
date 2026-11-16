import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, event, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Candidate(Base):
    __tablename__ = "candidates"
    __table_args__ = (
        CheckConstraint("length(trim(full_name)) > 0", name="ck_candidates_full_name_nonempty"),
        CheckConstraint("length(email) > 2", name="ck_candidates_email_nonempty"),
        CheckConstraint("email = lower(trim(email))", name="ck_candidates_email_canonical"),
        UniqueConstraint("email", name="uq_candidates_email"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(40))
    location: Mapped[str | None] = mapped_column(String(200))
    linkedin_url: Mapped[str | None] = mapped_column(String(2048))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    applications: Mapped[list["Application"]] = relationship(back_populates="candidate")


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("candidate_id", "job_id", name="uq_applications_candidate_job"),
        CheckConstraint("source IN ('public_portal', 'recruiter', 'referral')", name="ck_applications_source"),
        CheckConstraint(
            "stage IN ('applied', 'screening', 'shortlisted', 'interview', 'selected', 'offer', 'hired', 'rejected', 'withdrawn')",
            name="ck_applications_stage",
        ),
        Index("ix_applications_job_stage", "job_id", "stage"),
        Index("ix_applications_candidate", "candidate_id"),
        Index("ix_applications_source", "source"),
        Index("ix_applications_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidates.id", ondelete="RESTRICT"), nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    stage: Mapped[str] = mapped_column(String(20), nullable=False, default="applied", server_default="applied")
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    candidate: Mapped[Candidate] = relationship(back_populates="applications")
    job: Mapped["Job"] = relationship()
    notes: Mapped[list["ApplicationNote"]] = relationship(back_populates="application")
    documents: Mapped[list["StoredDocument"]] = relationship(back_populates="application")
    interview_rounds: Mapped[list["InterviewRound"]] = relationship(back_populates="application")
    offers: Mapped[list["Offer"]] = relationship(back_populates="application")


class ApplicationNote(Base):
    __tablename__ = "application_notes"
    __table_args__ = (Index("ix_application_notes_application_created", "application_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id", ondelete="RESTRICT"), nullable=False)
    author_employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    application: Mapped[Application] = relationship(back_populates="notes")
    author: Mapped["Employee"] = relationship()


class StoredDocument(Base):
    __tablename__ = "stored_documents"
    __table_args__ = (
        CheckConstraint("purpose = 'resume'", name="ck_stored_documents_purpose"),
        CheckConstraint("media_type = 'application/pdf'", name="ck_stored_documents_media_type"),
        CheckConstraint("byte_size > 0 AND byte_size <= 10485760", name="ck_stored_documents_size"),
        CheckConstraint("length(checksum_sha256) = 64", name="ck_stored_documents_checksum"),
        UniqueConstraint("object_key", name="uq_stored_documents_object_key"),
        Index("ix_stored_documents_application_created", "application_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id", ondelete="RESTRICT"), nullable=False)
    object_key: Mapped[str] = mapped_column(String(255), nullable=False)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False, default="resume", server_default="resume")
    media_type: Mapped[str] = mapped_column(String(100), nullable=False, default="application/pdf", server_default="application/pdf")
    byte_size: Mapped[int] = mapped_column(nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    application: Mapped[Application] = relationship(back_populates="documents")


from app.models.auth import Employee  # noqa: E402  Relationship type registration.
from app.models.jobs import Job  # noqa: E402
from app.models.interviews import InterviewRound  # noqa: E402  Relationship type registration.
from app.models.offers import Offer  # noqa: E402


@event.listens_for(ApplicationNote, "before_update")
def prevent_note_update(_mapper, _connection, _target) -> None:
    raise ValueError("Application notes are append-only")


@event.listens_for(ApplicationNote, "before_delete")
def prevent_note_delete(_mapper, _connection, _target) -> None:
    raise ValueError("Application notes are append-only")
