import enum
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, JSON, String, event, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class EmployeeRole(str, enum.Enum):
    ADMIN = "admin"
    HR_MANAGER = "hr_manager"
    RECRUITER = "recruiter"
    INTERVIEWER = "interviewer"


class EmployeeStatus(str, enum.Enum):
    INVITED = "invited"
    ACTIVE = "active"
    DISABLED = "disabled"


class Employee(Base):
    __tablename__ = "employees"
    __table_args__ = (
        CheckConstraint("length(email) > 2", name="ck_employee_email_nonempty"),
        Index("ix_employees_status_role", "status", "role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    role: Mapped[EmployeeRole] = mapped_column(
        Enum(
            EmployeeRole,
            values_callable=lambda values: [item.value for item in values],
            native_enum=False,
            create_constraint=True,
            name="employee_role",
        ),
        nullable=False,
    )
    status: Mapped[EmployeeStatus] = mapped_column(
        Enum(
            EmployeeStatus,
            values_callable=lambda values: [item.value for item in values],
            native_enum=False,
            create_constraint=True,
            name="employee_status",
        ),
        default=EmployeeStatus.INVITED,
        nullable=False,
    )
    password_hash: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    refresh_sessions: Mapped[list["RefreshSession"]] = relationship(back_populates="employee")


class EmployeeInvitation(Base):
    __tablename__ = "employee_invitations"
    __table_args__ = (
        Index("ix_employee_invitations_email_expires", "email", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[EmployeeRole] = mapped_column(
        Enum(
            EmployeeRole,
            values_callable=lambda values: [item.value for item in values],
            native_enum=False,
            create_constraint=True,
            name="invitation_role",
        ),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invited_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class RefreshSession(Base):
    __tablename__ = "refresh_sessions"
    __table_args__ = (Index("ix_refresh_sessions_employee_active", "employee_id", "revoked_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    employee: Mapped[Employee] = relationship(back_populates="refresh_sessions")


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    __table_args__ = (Index("ix_password_reset_employee_expiry", "employee_id", "expires_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        CheckConstraint(
            "action IN ('employee.updated', 'employee.role_changed', 'employee.activated', 'employee.deactivated', "
            "'job.created', 'job.updated', 'job.published', 'job.paused', 'job.closed', 'job.archived', "
            "'job.recruiter_assigned', 'job.recruiter_removed', 'candidate.created', 'candidate.updated', "
            "'application.created', 'application.updated', 'application.stage_changed', 'resume.uploaded', "
            "'resume.replaced', 'resume.downloaded', 'application.note_created', "
            "'application.created_after_deadline', 'interview.round_created', 'interview.scheduled', "
            "'interview.rescheduled', 'interview.cancelled', 'interview.completed', "
            "'interview.interviewer_assigned', 'interview.interviewer_removed', "
            "'interview.feedback_created', 'interview.feedback_modified', "
            "'offer.created', 'offer.revised', 'offer.submitted', 'offer.approved', 'offer.sent', "
            "'offer.accepted', 'offer.rejected', 'offer.withdrawn', 'offer.expired', 'offer.letter_downloaded')",
            name="ck_audit_log_action",
        ),
        CheckConstraint(
            "((CASE WHEN target_employee_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN target_job_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN target_candidate_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN target_application_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN target_document_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN target_offer_id IS NOT NULL THEN 1 ELSE 0 END)) = 1",
            name="ck_audit_log_single_target",
        ),
        CheckConstraint(
            "(actor_kind = 'employee' AND actor_employee_id IS NOT NULL) OR "
            "(actor_kind = 'public' AND actor_employee_id IS NULL)",
            name="ck_audit_log_actor",
        ),
        Index("ix_audit_logs_target_created", "target_employee_id", "created_at"),
        Index("ix_audit_logs_target_candidate_created", "target_candidate_id", "created_at"),
        Index("ix_audit_logs_target_application_created", "target_application_id", "created_at"),
        Index("ix_audit_logs_target_document_created", "target_document_id", "created_at"),
        Index("ix_audit_logs_target_offer_created", "target_offer_id", "created_at"),
        Index("ix_audit_logs_actor_created", "actor_employee_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    actor_employee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"))
    target_employee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"))
    target_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT", name="fk_audit_logs_target_job_id_jobs")
    )
    target_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("candidates.id", ondelete="RESTRICT", name="fk_audit_logs_target_candidate_id_candidates")
    )
    target_application_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("applications.id", ondelete="RESTRICT", name="fk_audit_logs_target_application_id_applications")
    )
    target_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("stored_documents.id", ondelete="RESTRICT", name="fk_audit_logs_target_document_id_stored_documents")
    )
    target_offer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("offers.id", ondelete="RESTRICT", name="fk_audit_logs_target_offer_id_offers")
    )
    actor_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="employee", server_default="employee")
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    details: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


@event.listens_for(AuditLog, "before_update")
def prevent_audit_update(_mapper, _connection, _target) -> None:
    raise ValueError("Audit records are append-only")


@event.listens_for(AuditLog, "before_delete")
def prevent_audit_delete(_mapper, _connection, _target) -> None:
    raise ValueError("Audit records are append-only")
