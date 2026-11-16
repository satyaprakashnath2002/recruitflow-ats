from datetime import date
import logging

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import utcnow
from app.core.time import company_today
from app.models.applications import Application, Candidate, StoredDocument
from app.models.auth import AuditLog, Employee, EmployeeRole
from app.models.jobs import Job, JobAssignment, JobStatus
from app.schemas.applications import ApplicationSource, CandidateInput
from app.services.private_storage import PrivateObjectStore

OPERATIONAL_TRANSITIONS = {
    "applied": {"screening"},
    "screening": {"shortlisted"},
    "shortlisted": {"interview"},
    "interview": {"selected"},
}
FINAL_TRANSITIONS = {"selected": {"offer"}, "offer": {"hired"}}
FINAL_STAGES = {"selected", "offer", "hired", "rejected", "withdrawn"}
TERMINAL_STAGES = {"hired", "rejected", "withdrawn"}
NONTERMINAL_STAGES = {"applied", "screening", "shortlisted", "interview", "selected", "offer"}
logger = logging.getLogger(__name__)


def api_error(code: int, status_text: str, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail={"status": status_text, "message": message})


def ensure_job_accepts_application(job: Job, *, public: bool, settings: Settings | None = None) -> bool:
    if job.status != JobStatus.PUBLISHED.value:
        raise api_error(400, "job_not_accepting_applications", "Job is not accepting applications")
    if job.application_deadline is None:
        return False
    after_deadline = company_today(settings) > job.application_deadline
    if public and after_deadline:
        raise api_error(400, "application_deadline_passed", "Application deadline has passed")
    return after_deadline


def has_job_assignment(db: Session, employee: Employee, job_id) -> bool:
    return db.scalar(
        select(JobAssignment.job_id).where(
            JobAssignment.job_id == job_id,
            JobAssignment.employee_id == employee.id,
        )
    ) is not None


def get_application(db: Session, application_id, *, lock: bool = False) -> Application | None:
    statement = select(Application).where(Application.id == application_id)
    if lock:
        statement = statement.with_for_update()
    application = db.scalar(statement)
    if application is not None and not lock:
        # Materialize this privacy-scoped relationship while the request session is active.
        _ = application.candidate
    return application


def authorize_application(db: Session, employee: Employee, application: Application) -> None:
    if employee.role in (EmployeeRole.ADMIN, EmployeeRole.HR_MANAGER):
        return
    if employee.role == EmployeeRole.RECRUITER:
        if has_job_assignment(db, employee, application.job_id):
            return
        raise api_error(404, "not_found", "Application not found")
    raise api_error(403, "forbidden", "Insufficient permissions")


def candidate_visible_to_employee(db: Session, employee: Employee, candidate_id) -> bool:
    if employee.role in (EmployeeRole.ADMIN, EmployeeRole.HR_MANAGER):
        return True
    if employee.role != EmployeeRole.RECRUITER:
        raise api_error(403, "forbidden", "Insufficient permissions")
    return db.scalar(
        select(Application.id)
        .join(JobAssignment, JobAssignment.job_id == Application.job_id)
        .where(Application.candidate_id == candidate_id, JobAssignment.employee_id == employee.id)
        .limit(1)
    ) is not None


def _audit(
    db: Session,
    *,
    action: str,
    target_field: str,
    target_id,
    actor: Employee | None,
    details: dict[str, object] | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_employee_id=actor.id if actor else None,
            actor_kind="employee" if actor else "public",
            **{target_field: target_id},
            action=action,
            details=details or {},
            created_at=utcnow(),
        )
    )


def get_or_create_candidate(db: Session, data: CandidateInput) -> tuple[Candidate, bool]:
    canonical_email = str(data.email).strip().lower()
    candidate = db.scalar(select(Candidate).where(Candidate.email == canonical_email))
    if candidate is not None:
        return candidate, False

    try:
        with db.begin_nested():
            candidate = Candidate(
                full_name=data.full_name,
                email=canonical_email,
                phone=data.phone,
                location=data.location,
                linkedin_url=data.linkedin_url,
            )
            db.add(candidate)
            db.flush()
        return candidate, True
    except IntegrityError:
        # A concurrent request may have won the unique-email insert. Reuse its row.
        candidate = db.scalar(select(Candidate).where(Candidate.email == canonical_email))
        if candidate is None:
            raise
        return candidate, False


def create_application(
    db: Session,
    *,
    job: Job,
    candidate_data: CandidateInput,
    source: ApplicationSource,
    actor: Employee | None,
    resume_bytes: bytes | None = None,
    resume_checksum: str | None = None,
    object_store: PrivateObjectStore | None = None,
    settings: Settings | None = None,
) -> tuple[Application, StoredDocument | None]:
    after_deadline = ensure_job_accepts_application(job, public=actor is None, settings=settings)
    object_key = None
    document = None
    try:
        # Validate storage availability before looking up Candidate/Application state.
        # Public duplicates and new submissions must follow the same storage failure path.
        if resume_bytes is not None:
            if object_store is None or resume_checksum is None:
                raise RuntimeError("Private document storage is not configured")
            object_key = object_store.put(resume_bytes)

        candidate, candidate_created = get_or_create_candidate(db, candidate_data)
        duplicate = db.scalar(
            select(Application.id).where(Application.candidate_id == candidate.id, Application.job_id == job.id)
        )
        if duplicate is not None:
            raise DuplicateApplication

        application = Application(
            candidate_id=candidate.id,
            job_id=job.id,
            source=source.value,
            stage="applied",
            created_by_id=actor.id if actor else None,
        )
        db.add(application)
        db.flush()

        if resume_bytes is not None:
            document = StoredDocument(
                application_id=application.id,
                object_key=object_key,
                purpose="resume",
                media_type="application/pdf",
                byte_size=len(resume_bytes),
                checksum_sha256=resume_checksum,
                uploaded_by_id=actor.id if actor else None,
            )
            db.add(document)
            db.flush()

        if candidate_created:
            _audit(db, action="candidate.created", target_field="target_candidate_id", target_id=candidate.id, actor=actor)
        _audit(db, action="application.created", target_field="target_application_id", target_id=application.id, actor=actor)
        if document is not None:
            _audit(db, action="resume.uploaded", target_field="target_document_id", target_id=document.id, actor=actor)
        if after_deadline and actor is not None:
            _audit(
                db,
                action="application.created_after_deadline",
                target_field="target_application_id",
                target_id=application.id,
                actor=actor,
                details={"job_deadline": job.application_deadline.isoformat() if job.application_deadline else None},
            )
        db.commit()
        db.refresh(application)
        if document is not None:
            db.refresh(document)
        return application, document
    except Exception:
        db.rollback()
        if object_key and object_store is not None:
            try:
                object_store.delete(object_key)
            except Exception:
                logger.exception("Failed to clean up uncommitted private document")
        raise


class DuplicateApplication(Exception):
    pass


def change_stage(db: Session, application: Application, *, target: str, actor: Employee) -> None:
    # Serialize all state changes at the service boundary so direct service
    # callers receive the same concurrency protection as the API route.
    application = db.scalar(
        select(Application)
        .where(Application.id == application.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if application is None:
        raise api_error(404, "not_found", "Application not found")
    current = application.stage
    allowed = OPERATIONAL_TRANSITIONS.get(current, set()) | FINAL_TRANSITIONS.get(current, set())
    if target in {"rejected", "withdrawn"} and current in NONTERMINAL_STAGES:
        allowed = allowed | {target}
    if target not in allowed:
        raise api_error(400, "invalid_transition", f"Cannot transition application from {current} to {target}")

    if target in FINAL_STAGES and actor.role != EmployeeRole.HR_MANAGER:
        raise api_error(400, "invalid_transition", "Only an HR Manager may set final application outcomes")
    if target in {"screening", "shortlisted", "interview"}:
        if actor.role == EmployeeRole.HR_MANAGER or actor.role == EmployeeRole.ADMIN:
            pass
        elif actor.role != EmployeeRole.RECRUITER:
            raise api_error(400, "invalid_transition", "Only Admin, HR Manager, or an assigned Recruiter may make operational transitions")
    try:
        application.stage = target
        _audit(
            db,
            action="application.stage_changed",
            target_field="target_application_id",
            target_id=application.id,
            actor=actor,
            details={"from": current, "to": target},
        )
        db.commit()
        db.refresh(application)
    except Exception:
        db.rollback()
        raise


def audit_candidate_update(db: Session, *, candidate: Candidate, actor: Employee, fields: list[str]) -> None:
    _audit(
        db,
        action="candidate.updated",
        target_field="target_candidate_id",
        target_id=candidate.id,
        actor=actor,
        details={"changed_fields": sorted(fields)},
    )


def audit_note_created(db: Session, *, application_id, note_id, actor: Employee) -> None:
    _audit(
        db,
        action="application.note_created",
        target_field="target_application_id",
        target_id=application_id,
        actor=actor,
        details={"note_id": str(note_id)},
    )
