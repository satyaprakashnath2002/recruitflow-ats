import logging
import uuid
from io import BytesIO

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.api.dependencies import get_current_employee, require_roles
from app.core.config import get_settings
from app.db.session import get_db
from app.models.applications import Application, ApplicationNote, Candidate, StoredDocument
from app.models.auth import Employee, EmployeeRole
from app.models.jobs import Job, JobAssignment
from app.schemas.applications import (
    ApplicationCreate,
    ApplicationNoteCreate,
    ApplicationNotePage,
    ApplicationNoteResponse,
    ApplicationPage,
    ApplicationPatch,
    ApplicationResponse,
    ApplicationSource,
    ApplicationStage,
    ApplicationStageChange,
    CandidateInput,
    CandidatePage,
    CandidateResponse,
    CandidateUpdate,
    DocumentResponse,
)
from app.services.application_management import (
    DuplicateApplication,
    _audit,
    api_error,
    audit_candidate_update,
    audit_note_created,
    authorize_application,
    candidate_visible_to_employee,
    change_stage,
    create_application,
    ensure_job_accepts_application,
    get_application,
    get_or_create_candidate,
    has_job_assignment,
)
from app.services.private_storage import (
    MAX_RESUME_BYTES,
    InvalidResume,
    LocalPrivateObjectStore,
    OversizedResume,
    PrivateObjectStore,
    validate_pdf,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["candidates", "applications"])
MANAGERS = require_roles(EmployeeRole.ADMIN, EmployeeRole.HR_MANAGER)


def get_private_object_store() -> PrivateObjectStore:
    return LocalPrivateObjectStore(get_settings().document_storage_path)


def _candidate_or_404(db: Session, candidate_id: uuid.UUID) -> Candidate:
    candidate = db.get(Candidate, candidate_id)
    if candidate is None:
        raise api_error(404, "not_found", "Candidate not found")
    return candidate


@router.post("/candidates", response_model=CandidateResponse, status_code=status.HTTP_201_CREATED)
def create_candidate(
    data: CandidateInput,
    actor: Employee = Depends(MANAGERS),
    db: Session = Depends(get_db),
) -> Candidate:
    candidate, created = get_or_create_candidate(db, data)
    if not created:
        db.rollback()
        raise api_error(409, "conflict", "Candidate with this email already exists")
    _audit(db, action="candidate.created", target_field="target_candidate_id", target_id=candidate.id, actor=actor)
    db.commit()
    db.refresh(candidate)
    return candidate


@router.get("/candidates", response_model=CandidatePage)
def list_candidates(
    search: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _actor: Employee = Depends(MANAGERS),
    db: Session = Depends(get_db),
) -> CandidatePage:
    criteria = []
    if search and search.strip():
        escaped = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        criteria.append(
            Candidate.full_name.ilike(f"%{escaped}%", escape="\\")
            | Candidate.email.ilike(f"%{escaped}%", escape="\\")
        )
    total = db.scalar(select(func.count(Candidate.id)).where(*criteria)) or 0
    candidates = db.scalars(
        select(Candidate).where(*criteria).order_by(Candidate.created_at.desc(), Candidate.id)
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    return CandidatePage(items=candidates, page=page, page_size=page_size, total=total)


@router.get("/candidates/{candidate_id}", response_model=CandidateResponse)
def get_candidate(
    candidate_id: uuid.UUID,
    actor: Employee = Depends(get_current_employee),
    db: Session = Depends(get_db),
) -> Candidate:
    candidate = _candidate_or_404(db, candidate_id)
    if not candidate_visible_to_employee(db, actor, candidate.id):
        raise api_error(404, "not_found", "Candidate not found")
    return candidate


@router.patch("/candidates/{candidate_id}", response_model=CandidateResponse)
def update_candidate(
    candidate_id: uuid.UUID,
    data: CandidateUpdate,
    actor: Employee = Depends(get_current_employee),
    db: Session = Depends(get_db),
) -> Candidate:
    candidate = db.scalar(select(Candidate).where(Candidate.id == candidate_id).with_for_update())
    if candidate is None:
        raise api_error(404, "not_found", "Candidate not found")
    if not candidate_visible_to_employee(db, actor, candidate.id):
        raise api_error(404, "not_found", "Candidate not found")
    changes = data.model_dump(exclude_unset=True)
    if not changes:
        raise api_error(422, "validation_error", "At least one Candidate field must be supplied")
    changed_fields = [field for field, value in changes.items() if getattr(candidate, field) != value]
    if changed_fields:
        for field in changed_fields:
            value = changes[field]
            setattr(candidate, field, value)
        audit_candidate_update(db, candidate=candidate, actor=actor, fields=changed_fields)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise api_error(409, "conflict", "Candidate email is already in use") from exc
        db.refresh(candidate)
    return candidate


@router.post("/applications", response_model=ApplicationResponse, status_code=status.HTTP_201_CREATED)
def create_employee_application(
    data: ApplicationCreate,
    actor: Employee = Depends(get_current_employee),
    db: Session = Depends(get_db),
    object_store: PrivateObjectStore = Depends(get_private_object_store),
) -> Application:
    if actor.role not in (EmployeeRole.ADMIN, EmployeeRole.HR_MANAGER, EmployeeRole.RECRUITER):
        raise api_error(403, "forbidden", "Insufficient permissions")
    if data.source == ApplicationSource.PUBLIC_PORTAL:
        raise api_error(422, "validation_error", "Public Portal source is reserved for public submissions")
    job = db.get(Job, data.job_id)
    if job is None:
        raise api_error(404, "not_found", "Job not found")
    if actor.role == EmployeeRole.RECRUITER and not has_job_assignment(db, actor, job.id):
        raise api_error(404, "not_found", "Job not found")
    try:
        application, _ = create_application(
            db,
            job=job,
            candidate_data=data.candidate,
            source=data.source,
            actor=actor,
            object_store=object_store,
        )
    except DuplicateApplication as exc:
        db.rollback()
        raise api_error(409, "conflict", "Candidate already has an application for this job") from exc
    except IntegrityError as exc:
        db.rollback()
        candidate_id = db.scalar(select(Candidate.id).where(Candidate.email == str(data.candidate.email).lower()))
        if candidate_id is not None and db.scalar(
            select(Application.id).where(Application.candidate_id == candidate_id, Application.job_id == data.job_id)
        ) is not None:
            raise api_error(409, "conflict", "Candidate already has an application for this job") from exc
        raise
    return application


@router.post("/public/jobs/{job_id}/applications", status_code=status.HTTP_202_ACCEPTED)
async def create_public_application(
    job_id: uuid.UUID,
    full_name: str = Form(min_length=1, max_length=200),
    email: str = Form(max_length=320),
    phone: str | None = Form(default=None, max_length=40),
    location: str | None = Form(default=None, max_length=200),
    linkedin_url: str | None = Form(default=None, max_length=2048),
    resume: UploadFile = File(...),
    db: Session = Depends(get_db),
    object_store: PrivateObjectStore = Depends(get_private_object_store),
) -> dict[str, str]:
    generic_response = {"message": "If the submission is eligible, it has been received."}
    job = db.get(Job, job_id)
    if job is None:
        raise api_error(404, "not_found", "Job not found")
    ensure_job_accepts_application(job, public=True)
    content = await resume.read(MAX_RESUME_BYTES + 1)
    try:
        checksum = validate_pdf(content)
        candidate_data = CandidateInput(
            full_name=full_name,
            email=email,
            phone=phone,
            location=location,
            linkedin_url=linkedin_url,
        )
    except OversizedResume as exc:
        raise api_error(413, "file_too_large", str(exc)) from exc
    except InvalidResume as exc:
        raise api_error(415, "invalid_file", str(exc)) from exc
    except ValueError as exc:
        raise api_error(422, "validation_error", "Invalid candidate details") from exc
    finally:
        await resume.close()

    try:
        create_application(
            db,
            job=job,
            candidate_data=candidate_data,
            source=ApplicationSource.PUBLIC_PORTAL,
            actor=None,
            resume_bytes=content,
            resume_checksum=checksum,
            object_store=object_store,
        )
    except DuplicateApplication:
        db.rollback()
        return generic_response
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(Application.id)
            .join(Candidate, Candidate.id == Application.candidate_id)
            .where(Application.job_id == job.id, Candidate.email == str(candidate_data.email).lower())
        )
        if existing is not None:
            return generic_response
        raise
    return generic_response


@router.get("/applications", response_model=ApplicationPage)
def list_applications(
    stage: ApplicationStage | None = None,
    job_id: uuid.UUID | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    actor: Employee = Depends(get_current_employee),
    db: Session = Depends(get_db),
) -> ApplicationPage:
    if actor.role not in (EmployeeRole.ADMIN, EmployeeRole.HR_MANAGER, EmployeeRole.RECRUITER):
        raise api_error(403, "forbidden", "Insufficient permissions")
    criteria = []
    if stage is not None:
        criteria.append(Application.stage == stage.value)
    if job_id is not None:
        criteria.append(Application.job_id == job_id)
    if actor.role == EmployeeRole.RECRUITER:
        criteria.append(Application.job_id.in_(select(JobAssignment.job_id).where(JobAssignment.employee_id == actor.id)))
    total = db.scalar(select(func.count(Application.id)).where(*criteria)) or 0
    applications = db.scalars(
        select(Application).options(joinedload(Application.candidate)).where(*criteria)
        .order_by(Application.created_at.desc(), Application.id)
        .offset((page - 1) * page_size).limit(page_size)
    ).unique().all()
    return ApplicationPage(items=applications, page=page, page_size=page_size, total=total)


@router.get("/applications/{application_id}", response_model=ApplicationResponse)
def get_application_route(
    application_id: uuid.UUID,
    actor: Employee = Depends(get_current_employee),
    db: Session = Depends(get_db),
) -> Application:
    application = get_application(db, application_id)
    if application is None:
        raise api_error(404, "not_found", "Application not found")
    authorize_application(db, actor, application)
    return application


@router.patch("/applications/{application_id}", response_model=ApplicationResponse)
def patch_application(
    application_id: uuid.UUID,
    _data: ApplicationPatch,
    actor: Employee = Depends(get_current_employee),
    db: Session = Depends(get_db),
) -> Application:
    application = get_application(db, application_id)
    if application is None:
        raise api_error(404, "not_found", "Application not found")
    authorize_application(db, actor, application)
    # The approved schema has no ordinary Application metadata/operational
    # columns. Candidate data, notes and stage each use their dedicated API.
    raise api_error(422, "no_editable_fields", "Application has no editable fields")


@router.post("/applications/{application_id}/stage", response_model=ApplicationResponse)
def update_application_stage(
    application_id: uuid.UUID,
    data: ApplicationStageChange,
    actor: Employee = Depends(get_current_employee),
    db: Session = Depends(get_db),
) -> Application:
    application = get_application(db, application_id, lock=True)
    if application is None:
        raise api_error(404, "not_found", "Application not found")
    authorize_application(db, actor, application)
    change_stage(db, application, target=data.stage.value, actor=actor)
    return application


@router.get("/applications/{application_id}/notes", response_model=ApplicationNotePage)
def list_application_notes(
    application_id: uuid.UUID,
    actor: Employee = Depends(get_current_employee),
    db: Session = Depends(get_db),
) -> ApplicationNotePage:
    application = get_application(db, application_id)
    if application is None:
        raise api_error(404, "not_found", "Application not found")
    authorize_application(db, actor, application)
    rows = db.execute(
        select(ApplicationNote, Employee)
        .join(Employee, Employee.id == ApplicationNote.author_employee_id)
        .where(ApplicationNote.application_id == application.id)
        .order_by(ApplicationNote.created_at, ApplicationNote.id)
    ).all()
    return ApplicationNotePage(
        items=[
            ApplicationNoteResponse(
                id=note.id,
                application_id=note.application_id,
                author_employee_id=note.author_employee_id,
                author_name=employee.full_name,
                body=note.body,
                created_at=note.created_at,
            )
            for note, employee in rows
        ]
    )


@router.post("/applications/{application_id}/notes", response_model=ApplicationNoteResponse, status_code=status.HTTP_201_CREATED)
def create_application_note(
    application_id: uuid.UUID,
    data: ApplicationNoteCreate,
    actor: Employee = Depends(get_current_employee),
    db: Session = Depends(get_db),
) -> ApplicationNoteResponse:
    application = get_application(db, application_id)
    if application is None:
        raise api_error(404, "not_found", "Application not found")
    authorize_application(db, actor, application)
    note = ApplicationNote(application_id=application.id, author_employee_id=actor.id, body=data.body)
    db.add(note)
    db.flush()
    audit_note_created(db, application_id=application.id, note_id=note.id, actor=actor)
    db.commit()
    db.refresh(note)
    return ApplicationNoteResponse(
        id=note.id,
        application_id=note.application_id,
        author_employee_id=note.author_employee_id,
        author_name=actor.full_name,
        body=note.body,
        created_at=note.created_at,
    )


def _readable_document_application(db: Session, doc: StoredDocument, actor: Employee) -> Application:
    application = get_application(db, doc.application_id)
    if application is None:
        raise api_error(404, "not_found", "Document not found")
    authorize_application(db, actor, application)
    return application


@router.post("/applications/{application_id}/resume", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
@router.put("/applications/{application_id}/resume", response_model=DocumentResponse)
async def upload_application_resume(
    application_id: uuid.UUID,
    resume: UploadFile = File(...),
    actor: Employee = Depends(get_current_employee),
    db: Session = Depends(get_db),
    object_store: PrivateObjectStore = Depends(get_private_object_store),
) -> StoredDocument:
    application = get_application(db, application_id, lock=True)
    if application is None:
        raise api_error(404, "not_found", "Application not found")
    authorize_application(db, actor, application)
    content = await resume.read(MAX_RESUME_BYTES + 1)
    await resume.close()
    try:
        checksum = validate_pdf(content)
    except OversizedResume as exc:
        raise api_error(413, "file_too_large", str(exc)) from exc
    except InvalidResume as exc:
        raise api_error(415, "invalid_file", str(exc)) from exc

    object_key = None
    try:
        object_key = object_store.put(content)
        document = StoredDocument(
            application_id=application.id,
            object_key=object_key,
            purpose="resume",
            media_type="application/pdf",
            byte_size=len(content),
            checksum_sha256=checksum,
            uploaded_by_id=actor.id,
        )
        old_exists = db.scalar(
            select(StoredDocument.id).where(StoredDocument.application_id == application.id).limit(1)
        ) is not None
        db.add(document)
        db.flush()
        _audit(
            db,
            action="resume.replaced" if old_exists else "resume.uploaded",
            target_field="target_document_id",
            target_id=document.id,
            actor=actor,
        )
        db.commit()
    except Exception:
        db.rollback()
        if object_key is not None:
            try:
                object_store.delete(object_key)
            except Exception:
                logger.exception("Failed to clean up uncommitted private document")
        raise
    db.refresh(document)
    return document


@router.get("/documents/{document_id}")
def download_document(
    document_id: uuid.UUID,
    actor: Employee = Depends(get_current_employee),
    db: Session = Depends(get_db),
    object_store: PrivateObjectStore = Depends(get_private_object_store),
) -> StreamingResponse:
    document = db.get(StoredDocument, document_id)
    if document is None:
        raise api_error(404, "not_found", "Document not found")
    _readable_document_application(db, document, actor)
    try:
        content = object_store.read(document.object_key)
    except OSError as exc:
        logger.error("Private document retrieval failed for document_id=%s", document.id)
        raise api_error(503, "document_unavailable", "Resume is temporarily unavailable") from exc
    _audit(
        db,
        action="resume.downloaded",
        target_field="target_document_id",
        target_id=document.id,
        actor=actor,
    )
    db.commit()
    return StreamingResponse(
        BytesIO(content),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="resume-{document.id}.pdf"', "Cache-Control": "private, no-store"},
    )
