import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies import require_roles
from app.api.v1.auth import get_email_sender
from app.api.v1.applications import get_private_object_store
from app.db.session import get_db
from app.models.auth import Employee, EmployeeRole
from app.models.offers import Offer, OfferRevision
from app.schemas.offers import (
    OfferCandidateResponse,
    OfferPage,
    OfferPublicResponse,
    OfferResponse as OfferResponseSchema,
    OfferResponseReceipt,
    OfferRevisionPage,
    OfferRevisionResponse,
    OfferStatus as OfferStatusSchema,
    OfferTermsInput,
)
from app.services.email_delivery import EmailSender
from app.services.offers import (
    approve_offer,
    create_offer,
    current_revision,
    download_revision,
    get_candidate_offer,
    record_letter_download,
    respond_to_offer,
    revise_offer,
    send_offer,
    submit_offer,
    withdraw_offer,
)
from app.services.private_storage import (
    InvalidResume,
    MAX_RESUME_BYTES,
    OversizedResume,
    PrivateObjectStore,
    validate_pdf,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["offers"])
EMPLOYEE_OFFER_ROLES = require_roles(EmployeeRole.ADMIN, EmployeeRole.HR_MANAGER)
HR_MANAGER_ONLY = require_roles(EmployeeRole.HR_MANAGER)


def _offer_response(db: Session, offer: Offer) -> OfferResponseSchema:
    revision = current_revision(db, offer)
    return OfferResponseSchema(
        id=offer.id,
        application_id=offer.application_id,
        status=offer.status,
        current_revision_number=offer.current_revision_number,
        approved_revision_number=offer.approved_revision_number,
        approved_by_employee_id=offer.approved_by_employee_id,
        approved_at=offer.approved_at,
        sent_at=offer.sent_at,
        responded_at=offer.responded_at,
        withdrawn_at=offer.withdrawn_at,
        expired_at=offer.expired_at,
        created_by_employee_id=offer.created_by_employee_id,
        created_at=offer.created_at,
        updated_at=offer.updated_at,
        current_revision=OfferRevisionResponse(
            id=revision.id,
            revision_number=revision.revision_number,
            compensation_amount=revision.compensation_amount,
            compensation_currency=revision.compensation_currency,
            compensation_basis=revision.compensation_basis,
            expires_at=revision.expires_at,
            letter_size=revision.byte_size,
            created_by_employee_id=revision.created_by_employee_id,
            created_at=revision.created_at,
        ),
    )


async def _read_offer_letter(resume: UploadFile) -> tuple[bytes, str]:
    content = await resume.read(MAX_RESUME_BYTES + 1)
    await resume.close()
    try:
        checksum = validate_pdf(content)
    except OversizedResume as exc:
        raise HTTPException(status_code=413, detail={"status": "file_too_large", "message": "Offer letter exceeds the 10 MiB limit"}) from exc
    except InvalidResume as exc:
        raise HTTPException(status_code=415, detail={"status": "invalid_file", "message": "Offer letter must contain a valid PDF"}) from exc
    return content, checksum


def _private_pdf(content: bytes, filename: str = "offer-letter.pdf") -> Response:
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/applications/{application_id}/offers",
    response_model=OfferResponseSchema,
    status_code=status.HTTP_201_CREATED,
    summary="Create a private draft Offer",
)
async def create_offer_route(
    application_id: uuid.UUID,
    terms: Annotated[OfferTermsInput, Form()],
    letter: UploadFile = File(...),
    actor: Employee = Depends(EMPLOYEE_OFFER_ROLES),
    db: Session = Depends(get_db),
    object_store: PrivateObjectStore = Depends(get_private_object_store),
) -> OfferResponseSchema:
    content, checksum = await _read_offer_letter(letter)
    object_key = object_store.put(content)
    try:
        offer = create_offer(
            db,
            application_id=application_id,
            actor=actor,
            terms=terms,
            object_key=object_key,
            byte_size=len(content),
            checksum_sha256=checksum,
        )
    except Exception:
        db.rollback()
        try:
            object_store.delete(object_key)
        except Exception:
            logger.exception("Failed to clean up an uncommitted private Offer letter")
        raise
    return _offer_response(db, offer)


@router.get("/offers", response_model=OfferPage, summary="List Offers for Admin and HR Manager")
def list_offers(
    status_filter: OfferStatusSchema | None = Query(default=None, alias="status"),
    application_id: uuid.UUID | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _actor: Employee = Depends(EMPLOYEE_OFFER_ROLES),
    db: Session = Depends(get_db),
) -> OfferPage:
    criteria = []
    if status_filter is not None:
        criteria.append(Offer.status == status_filter.value)
    if application_id is not None:
        criteria.append(Offer.application_id == application_id)
    total = db.scalar(select(func.count(Offer.id)).where(*criteria)) or 0
    items = db.scalars(
        select(Offer).where(*criteria).order_by(Offer.created_at.desc(), Offer.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    return OfferPage(items=[_offer_response(db, offer) for offer in items], page=page, page_size=page_size, total=total)


@router.get("/offers/{offer_id}", response_model=OfferResponseSchema, summary="Read an Offer")
def read_offer(
    offer_id: uuid.UUID,
    _actor: Employee = Depends(EMPLOYEE_OFFER_ROLES),
    db: Session = Depends(get_db),
) -> OfferResponseSchema:
    offer = db.get(Offer, offer_id)
    if offer is None:
        raise HTTPException(status_code=404, detail={"status": "not_found", "message": "Offer not found"})
    return _offer_response(db, offer)


@router.post("/offers/{offer_id}/revisions", response_model=OfferResponseSchema, summary="Create an immutable draft revision")
async def revise_offer_route(
    offer_id: uuid.UUID,
    terms: Annotated[OfferTermsInput, Form()],
    letter: UploadFile = File(...),
    actor: Employee = Depends(EMPLOYEE_OFFER_ROLES),
    db: Session = Depends(get_db),
    object_store: PrivateObjectStore = Depends(get_private_object_store),
) -> OfferResponseSchema:
    content, checksum = await _read_offer_letter(letter)
    object_key = object_store.put(content)
    try:
        offer = revise_offer(
            db,
            offer_id=offer_id,
            actor=actor,
            terms=terms,
            object_key=object_key,
            byte_size=len(content),
            checksum_sha256=checksum,
        )
    except Exception:
        db.rollback()
        try:
            object_store.delete(object_key)
        except Exception:
            logger.exception("Failed to clean up an uncommitted private Offer letter revision")
        raise
    return _offer_response(db, offer)


@router.get("/offers/{offer_id}/revisions", response_model=OfferRevisionPage, summary="List immutable Offer revisions")
def list_offer_revisions(
    offer_id: uuid.UUID,
    _actor: Employee = Depends(EMPLOYEE_OFFER_ROLES),
    db: Session = Depends(get_db),
) -> OfferRevisionPage:
    if db.get(Offer, offer_id) is None:
        raise HTTPException(status_code=404, detail={"status": "not_found", "message": "Offer not found"})
    rows = db.scalars(select(OfferRevision).where(OfferRevision.offer_id == offer_id).order_by(OfferRevision.revision_number)).all()
    return OfferRevisionPage(items=[OfferRevisionResponse(
        id=row.id,
        revision_number=row.revision_number,
        compensation_amount=row.compensation_amount,
        compensation_currency=row.compensation_currency,
        compensation_basis=row.compensation_basis,
        expires_at=row.expires_at,
        letter_size=row.byte_size,
        created_by_employee_id=row.created_by_employee_id,
        created_at=row.created_at,
    ) for row in rows])


@router.get("/offers/{offer_id}/revisions/{revision_number}/letter", summary="Download a private Offer letter")
def download_employee_offer_letter(
    offer_id: uuid.UUID,
    revision_number: int,
    _actor: Employee = Depends(EMPLOYEE_OFFER_ROLES),
    db: Session = Depends(get_db),
    object_store: PrivateObjectStore = Depends(get_private_object_store),
) -> Response:
    offer = db.get(Offer, offer_id)
    if offer is None:
        raise HTTPException(status_code=404, detail={"status": "not_found", "message": "Offer not found"})
    revision = download_revision(db, offer_id=offer_id, revision_number=revision_number)
    content = object_store.read(revision.letter_object_key)
    record_letter_download(db, offer=offer, actor=_actor, revision_number=revision_number)
    return _private_pdf(content)


@router.post("/offers/{offer_id}/submit", response_model=OfferResponseSchema, summary="Submit a draft Offer for approval")
def submit_offer_route(
    offer_id: uuid.UUID,
    actor: Employee = Depends(EMPLOYEE_OFFER_ROLES),
    db: Session = Depends(get_db),
) -> OfferResponseSchema:
    return _offer_response(db, submit_offer(db, offer_id=offer_id, actor=actor))


@router.post("/offers/{offer_id}/approve", response_model=OfferResponseSchema, summary="Approve an Offer as HR Manager")
def approve_offer_route(
    offer_id: uuid.UUID,
    actor: Employee = Depends(HR_MANAGER_ONLY),
    db: Session = Depends(get_db),
) -> OfferResponseSchema:
    return _offer_response(db, approve_offer(db, offer_id=offer_id, actor=actor))


@router.post("/offers/{offer_id}/send", response_model=OfferResponseSchema, summary="Send an approved Offer to its Candidate")
def send_offer_route(
    offer_id: uuid.UUID,
    actor: Employee = Depends(HR_MANAGER_ONLY),
    db: Session = Depends(get_db),
    email_sender: EmailSender = Depends(get_email_sender),
) -> OfferResponseSchema:
    return _offer_response(db, send_offer(db, offer_id=offer_id, actor=actor, email_sender=email_sender))


@router.post("/offers/{offer_id}/withdraw", response_model=OfferResponseSchema, summary="Withdraw an active Offer as HR Manager")
def withdraw_offer_route(
    offer_id: uuid.UUID,
    actor: Employee = Depends(HR_MANAGER_ONLY),
    db: Session = Depends(get_db),
) -> OfferResponseSchema:
    return _offer_response(db, withdraw_offer(db, offer_id=offer_id, actor=actor))


@router.get("/public/offers/current", response_model=OfferPublicResponse, summary="View the Offer scoped to an email capability")
def read_public_offer(
    response: Response,
    capability: str = Header(alias="X-Offer-Capability"),
    db: Session = Depends(get_db),
) -> OfferPublicResponse:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "X-Offer-Capability"
    offer, revision, job_title = get_candidate_offer(db, capability)
    return OfferPublicResponse(
        job_title=job_title,
        compensation_amount=revision.compensation_amount,
        compensation_currency=revision.compensation_currency,
        compensation_basis=revision.compensation_basis,
        expires_at=revision.expires_at,
        response=offer.status if offer.status in {"accepted", "rejected"} else None,
    )


@router.get("/public/offers/letter", summary="Download the Offer letter using its capability")
def download_public_offer_letter(
    capability: str = Header(alias="X-Offer-Capability"),
    db: Session = Depends(get_db),
    object_store: PrivateObjectStore = Depends(get_private_object_store),
) -> Response:
    offer, revision, _ = get_candidate_offer(db, capability)
    content = object_store.read(revision.letter_object_key)
    record_letter_download(db, offer=offer, actor=None, revision_number=revision.revision_number)
    return _private_pdf(content)


@router.post("/public/offers/response", response_model=OfferResponseReceipt, summary="Accept or reject this Offer once")
def respond_public_offer(
    response: Response,
    data: OfferCandidateResponse,
    capability: str = Header(alias="X-Offer-Capability"),
    db: Session = Depends(get_db),
) -> OfferResponseReceipt:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "X-Offer-Capability"
    offer = respond_to_offer(db, capability=capability, response=data.response)
    return OfferResponseReceipt(status=offer.status, responded_at=offer.responded_at)
