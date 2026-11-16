import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class JobStatus(str, enum.Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    PAUSED = "paused"
    CLOSED = "closed"
    ARCHIVED = "archived"


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("length(title) > 0", name="ck_jobs_title_nonempty"),
        CheckConstraint("length(department) > 0", name="ck_jobs_department_nonempty"),
        CheckConstraint("length(employment_type) > 0", name="ck_jobs_employment_type_nonempty"),
        CheckConstraint("length(location) > 0", name="ck_jobs_location_nonempty"),
        CheckConstraint("length(work_mode) > 0", name="ck_jobs_work_mode_nonempty"),
        CheckConstraint("length(description) > 0", name="ck_jobs_description_nonempty"),
        CheckConstraint("vacancies > 0", name="ck_jobs_vacancies_positive"),
        CheckConstraint(
            "min_experience IS NULL OR min_experience >= 0", name="ck_jobs_min_experience_nonnegative"
        ),
        CheckConstraint(
            "max_experience IS NULL OR max_experience >= 0", name="ck_jobs_max_experience_nonnegative"
        ),
        CheckConstraint(
            "min_experience IS NULL OR max_experience IS NULL OR min_experience <= max_experience",
            name="ck_jobs_experience_range",
        ),
        CheckConstraint("salary_min IS NULL OR salary_min >= 0", name="ck_jobs_salary_min_nonnegative"),
        CheckConstraint("salary_max IS NULL OR salary_max >= 0", name="ck_jobs_salary_max_nonnegative"),
        CheckConstraint(
            "salary_min IS NULL OR salary_max IS NULL OR salary_min <= salary_max", name="ck_jobs_salary_range"
        ),
        CheckConstraint(
            "status IN ('draft', 'published', 'paused', 'closed', 'archived')", name="ck_jobs_status"
        ),
        Index("ix_jobs_status_created", "status", "created_at"),
        Index("ix_jobs_department", "department"),
        Index("ix_jobs_employment_type", "employment_type"),
        Index("ix_jobs_location", "location"),
        Index("ix_jobs_work_mode", "work_mode"),
        Index("ix_jobs_experience", "min_experience", "max_experience"),
        Index("ix_jobs_deadline", "application_deadline"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    department: Mapped[str] = mapped_column(String(120), nullable=False)
    employment_type: Mapped[str] = mapped_column(String(50), nullable=False)
    location: Mapped[str] = mapped_column(String(200), nullable=False)
    work_mode: Mapped[str] = mapped_column(String(50), nullable=False)
    min_experience: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    max_experience: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    salary_min: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    salary_max: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    description: Mapped[str] = mapped_column(Text, nullable=False)
    required_skills: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    qualifications: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    vacancies: Mapped[int] = mapped_column(nullable=False)
    application_deadline: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=JobStatus.DRAFT.value)
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False
    )
    updated_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    created_by: Mapped["Employee"] = relationship(foreign_keys=[created_by_id])
    updated_by: Mapped["Employee"] = relationship(foreign_keys=[updated_by_id])
    assignments: Mapped[list["JobAssignment"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class JobAssignment(Base):
    __tablename__ = "job_assignments"
    __table_args__ = (
        Index("ix_job_assignments_employee", "employee_id"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="RESTRICT"), primary_key=True
    )
    assigned_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False
    )
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    job: Mapped[Job] = relationship(back_populates="assignments")
    recruiter: Mapped["Employee"] = relationship(foreign_keys=[employee_id])
    assigned_by: Mapped["Employee"] = relationship(foreign_keys=[assigned_by_id])
