"""Offer lifecycle, scoped capability access, audit, and expiry reminders."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import secrets
import uuid

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_opaque_token, new_opaque_token, utcnow
from app.models.applications import Application
from app.models.auth import AuditLog, Employee, EmployeeRole, EmployeeStatus
from app.models.jobs import Job
from app.models.notifications import Notification, ScheduledOfferReminder
from app.models.offers import Offer, OfferRevision, OfferStatus
from app.schemas.offers import OfferTermsInput
from app.services.email_delivery import EmailDeliveryUnavailable, EmailSender


ACTIVE_OFFER_STATUSES = ("draft", "pending_approval", "approved", "sent")
_REMINDER_DAYS = (3, 1)


def _error(code: int, status: str, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail={"status": status, "message": message})


def _role(actor: Employee, *allowed: EmployeeRole) -> None:
    if actor.role not in allowed:
        raise _error(403, "forbidden", "Insufficient permissions")


def _audit(db: Session, offer: Offer, action: str, actor: Employee | None, **details: object) -> None:
    db.add(AuditLog(
        actor_employee_id=actor.id if actor else None,
        actor_kind="employee" if actor else "public",
        target_offer_id=offer.id,
        action=action,
        details=details,
    ))


def _lock_offer_context(db: Session, offer_id: uuid.UUID) -> tuple[Offer, Application]:
    application_id = db.scalar(select(Offer.application_id).where(Offer.id == offer_id))
    if application_id is None:
        raise _error(404, "not_found", "Offer not found")
    application = db.scalar(
        select(Application).where(Application.id == application_id).with_for_update().execution_options(populate_existing=True)
    )
    if application is None:
        raise _error(404, "not_found", "Offer not found")
    offer = db.scalar(
        select(Offer).where(Offer.id == offer_id).with_for_update().execution_options(populate_existing=True)
    )
    if offer is None:
        raise _error(404, "not_found", "Offer not found")
    return offer, application


def current_revision(db: Session, offer: Offer) -> OfferRevision:
    revision = db.scalar(select(OfferRevision).where(
        OfferRevision.offer_id == offer.id,
        OfferRevision.revision_number == offer.current_revision_number,
    ))
    if revision is None:
        raise _error(500, "offer_integrity_error", "Offer revision is unavailable")
    return revision


def _validate_terms(terms: OfferTermsInput) -> None:
    if _as_utc(terms.expires_at) <= utcnow():
        raise _error(422, "invalid_offer", "Offer expiry must be in the future")


def _as_utc(value: datetime) -> datetime:
    """Normalize stored timestamps; SQLite may return naive UTC datetimes."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _validate_stored_expiry(expires_at: datetime) -> None:
    # SQLite drops timezone offsets from DateTime values. Treat a naive value
    # loaded from the database as UTC; API inputs are validated as aware.
    if _as_utc(expires_at) <= utcnow():
        raise _error(422, "invalid_offer", "Offer expiry must be in the future")


def create_offer(
    db: Session,
    *,
    application_id: uuid.UUID,
    actor: Employee,
    terms: OfferTermsInput,
    object_key: str,
    byte_size: int,
    checksum_sha256: str,
) -> Offer:
    _role(actor, EmployeeRole.ADMIN, EmployeeRole.HR_MANAGER)
    _validate_terms(terms)
    application = db.scalar(
        select(Application).where(Application.id == application_id).with_for_update().execution_options(populate_existing=True)
    )
    if application is None:
        raise _error(404, "not_found", "Application not found")
    if application.stage not in {"selected", "offer"}:
        raise _error(409, "application_not_selected", "Offers can be created only for selected Applications")
    previous = db.scalars(select(Offer).where(Offer.application_id == application.id)).all()
    if any(offer.status in {"accepted", "rejected"} for offer in previous):
        raise _error(409, "offer_response_final", "A responded Offer cannot be replaced")
    if any(offer.status in ACTIVE_OFFER_STATUSES for offer in previous):
        raise _error(409, "active_offer_exists", "An active Offer already exists for this Application")
    offer = Offer(
        application_id=application.id,
        created_by_employee_id=actor.id,
        status=OfferStatus.DRAFT.value,
        current_revision_number=1,
    )
    db.add(offer)
    db.flush()
    revision = OfferRevision(
        offer_id=offer.id,
        revision_number=1,
        compensation_amount=terms.compensation_amount,
        compensation_currency=terms.compensation_currency,
        compensation_basis=terms.compensation_basis,
        expires_at=terms.expires_at,
        letter_object_key=object_key,
        byte_size=byte_size,
        checksum_sha256=checksum_sha256,
        created_by_employee_id=actor.id,
    )
    db.add(revision)
    _audit(db, offer, "offer.created", actor, revision_number=1)
    db.commit()
    db.refresh(offer)
    return offer


def revise_offer(
    db: Session,
    *,
    offer_id: uuid.UUID,
    actor: Employee,
    terms: OfferTermsInput,
    object_key: str,
    byte_size: int,
    checksum_sha256: str,
) -> Offer:
    _role(actor, EmployeeRole.ADMIN, EmployeeRole.HR_MANAGER)
    _validate_terms(terms)
    offer, _ = _lock_offer_context(db, offer_id)
    if offer.status != OfferStatus.DRAFT.value:
        raise _error(409, "offer_not_editable", "Only draft Offers may be revised")
    number = offer.current_revision_number + 1
    db.add(OfferRevision(
        offer_id=offer.id,
        revision_number=number,
        compensation_amount=terms.compensation_amount,
        compensation_currency=terms.compensation_currency,
        compensation_basis=terms.compensation_basis,
        expires_at=terms.expires_at,
        letter_object_key=object_key,
        byte_size=byte_size,
        checksum_sha256=checksum_sha256,
        created_by_employee_id=actor.id,
    ))
    offer.current_revision_number = number
    _audit(db, offer, "offer.revised", actor, revision_number=number)
    db.commit()
    db.refresh(offer)
    return offer


def submit_offer(db: Session, *, offer_id: uuid.UUID, actor: Employee) -> Offer:
    _role(actor, EmployeeRole.ADMIN, EmployeeRole.HR_MANAGER)
    offer, _ = _lock_offer_context(db, offer_id)
    if offer.status != OfferStatus.DRAFT.value:
        raise _error(409, "invalid_offer_transition", "Only draft Offers may be submitted for approval")
    revision = current_revision(db, offer)
    _validate_stored_expiry(revision.expires_at)
    offer.status = OfferStatus.PENDING_APPROVAL.value
    _audit(db, offer, "offer.submitted", actor, revision_number=offer.current_revision_number)
    db.commit()
    db.refresh(offer)
    return offer


def approve_offer(db: Session, *, offer_id: uuid.UUID, actor: Employee) -> Offer:
    _role(actor, EmployeeRole.HR_MANAGER)
    offer, _ = _lock_offer_context(db, offer_id)
    if offer.status != OfferStatus.PENDING_APPROVAL.value:
        raise _error(409, "invalid_offer_transition", "Only pending Offers may be approved")
    revision = current_revision(db, offer)
    _validate_stored_expiry(revision.expires_at)
    offer.status = OfferStatus.APPROVED.value
    offer.approved_revision_number = offer.current_revision_number
    offer.approved_by_employee_id = actor.id
    offer.approved_at = utcnow()
    _audit(db, offer, "offer.approved", actor, revision_number=offer.current_revision_number)
    db.commit()
    db.refresh(offer)
    return offer


def _supersede_reminders(db: Session, offer_id: uuid.UUID) -> None:
    for reminder in db.scalars(select(ScheduledOfferReminder).where(
        ScheduledOfferReminder.offer_id == offer_id,
        ScheduledOfferReminder.state == "pending",
    ).with_for_update()):
        reminder.state = "superseded"


def _schedule_reminders(db: Session, offer: Offer, revision: OfferRevision) -> None:
    if offer.approved_by_employee_id is None:
        raise _error(409, "offer_approval_required", "Only an approved Offer may be sent")
    now = utcnow()
    _supersede_reminders(db, offer.id)
    for days in _REMINDER_DAYS:
        scheduled = _as_utc(revision.expires_at) - timedelta(days=days)
        if scheduled <= now:
            continue
        key = f"offer-expiry:{offer.id}:{revision.id}:{offer.approved_by_employee_id}:{days}"
        db.add(ScheduledOfferReminder(
            offer_id=offer.id,
            offer_revision_id=revision.id,
            recipient_employee_id=offer.approved_by_employee_id,
            offset_days=days,
            expires_at_snapshot=revision.expires_at,
            scheduled_at=scheduled,
            state="pending",
            idempotency_key=key,
        ))


def send_offer(
    db: Session,
    *,
    offer_id: uuid.UUID,
    actor: Employee,
    email_sender: EmailSender,
) -> Offer:
    _role(actor, EmployeeRole.HR_MANAGER)
    offer, application = _lock_offer_context(db, offer_id)
    if offer.status != OfferStatus.APPROVED.value:
        raise _error(409, "offer_approval_required", "Only an approved Offer may be sent")
    if offer.approved_revision_number != offer.current_revision_number:
        raise _error(409, "approval_outdated", "The current Offer revision has not been approved")
    if offer.approved_by_employee_id is None:
        raise _error(409, "offer_approval_required", "HR Manager approval is required before sending")
    revision = current_revision(db, offer)
    _validate_stored_expiry(revision.expires_at)
    if application.stage not in {"selected", "offer"}:
        raise _error(409, "application_stage_conflict", "Application must remain Selected or Offer when the Offer is sent")

    capability = new_opaque_token()
    expiry = _as_utc(revision.expires_at)
    link = f"{get_settings().frontend_base_url.rstrip('/')}/offer-response#capability={capability}"
    try:
        candidate_email = application.candidate.email
        email_sender.send(
            candidate_email,
            "Your RecruitFlow offer is ready",
            f"A private offer is ready for review. Open this secure link before {expiry.isoformat()}:\n{link}\n"
            "This link grants access to this offer only. Do not forward it.",
        )
    except EmailDeliveryUnavailable as exc:
        raise _error(503, "delivery_unavailable", "Offer delivery is not configured") from exc
    except Exception as exc:
        raise _error(502, "delivery_failed", "Offer delivery failed; the Offer remains approved") from exc

    now = utcnow()
    offer.status = OfferStatus.SENT.value
    offer.sent_at = now
    offer.capability_token_hash = hash_opaque_token(capability)
    offer.capability_expires_at = expiry
    offer.capability_revoked_at = None
    if application.stage == "selected":
        application.stage = "offer"
        db.add(AuditLog(
            actor_employee_id=actor.id,
            actor_kind="employee",
            target_application_id=application.id,
            action="application.stage_changed",
            details={"from": "selected", "to": "offer"},
        ))
    _schedule_reminders(db, offer, revision)
    db.add(Notification(
        recipient_employee_id=offer.created_by_employee_id,
        event_type="offer_sent",
        title="Offer sent",
        body="An approved offer was delivered to its candidate.",
        resource_type="offer",
        resource_id=None,
        offer_id=offer.id,
        idempotency_key=f"offer-sent:{offer.id}:{offer.current_revision_number}",
    ))
    _audit(db, offer, "offer.sent", actor, revision_number=offer.current_revision_number)
    try:
        db.commit()
        db.refresh(offer)
    except Exception:
        db.rollback()
        raise
    return offer


def withdraw_offer(db: Session, *, offer_id: uuid.UUID, actor: Employee) -> Offer:
    _role(actor, EmployeeRole.HR_MANAGER)
    offer, _ = _lock_offer_context(db, offer_id)
    if offer.status not in ACTIVE_OFFER_STATUSES:
        raise _error(409, "invalid_offer_transition", "This Offer can no longer be withdrawn")
    offer.status = OfferStatus.WITHDRAWN.value
    offer.withdrawn_at = utcnow()
    offer.capability_revoked_at = offer.withdrawn_at
    _supersede_reminders(db, offer.id)
    _audit(db, offer, "offer.withdrawn", actor, revision_number=offer.current_revision_number)
    db.commit()
    db.refresh(offer)
    return offer


def _capability_offer(db: Session, capability: str) -> Offer:
    if not capability or len(capability) > 128:
        raise _error(404, "offer_link_invalid", "Offer link is invalid or expired")
    offer = db.scalar(select(Offer).where(Offer.capability_token_hash == hash_opaque_token(capability)))
    now = utcnow()
    if (
        offer is None
        or offer.capability_revoked_at is not None
        or offer.capability_expires_at is None
        or _as_utc(offer.capability_expires_at) <= now
        or offer.status not in {"sent", "accepted", "rejected"}
    ):
        raise _error(404, "offer_link_invalid", "Offer link is invalid or expired")
    return offer


def get_candidate_offer(db: Session, capability: str) -> tuple[Offer, OfferRevision, str]:
    offer = _capability_offer(db, capability)
    revision = current_revision(db, offer)
    job_title = db.scalar(select(Job.title).join(Application, Job.id == Application.job_id).where(Application.id == offer.application_id))
    if job_title is None:
        raise _error(404, "offer_link_invalid", "Offer link is invalid or expired")
    response = offer.status if offer.status in {"accepted", "rejected"} else None
    return offer, revision, job_title


def respond_to_offer(db: Session, *, capability: str, response: str) -> Offer:
    token_offer = _capability_offer(db, capability)
    offer, _ = _lock_offer_context(db, token_offer.id)
    # Recheck the token/state after row locks to prevent replay and expiry races.
    if offer.capability_token_hash != hash_opaque_token(capability):
        raise _error(404, "offer_link_invalid", "Offer link is invalid or expired")
    if offer.status != OfferStatus.SENT.value:
        raise _error(409, "offer_already_responded", "This Offer has already received a response")
    revision = current_revision(db, offer)
    if _as_utc(revision.expires_at) <= utcnow():
        offer.status = OfferStatus.EXPIRED.value
        offer.expired_at = utcnow()
        offer.capability_revoked_at = offer.expired_at
        _supersede_reminders(db, offer.id)
        _audit(db, offer, "offer.expired", None, revision_number=offer.current_revision_number)
        db.commit()
        raise _error(410, "offer_expired", "Offer response deadline has passed")
    if response not in {"accepted", "rejected"}:
        raise _error(422, "invalid_response", "Response must be accepted or rejected")
    offer.status = response
    offer.responded_at = utcnow()
    _supersede_reminders(db, offer.id)
    _audit(db, offer, f"offer.{response}", None, revision_number=offer.current_revision_number)
    db.commit()
    db.refresh(offer)
    return offer


def download_revision(db: Session, *, offer_id: uuid.UUID, revision_number: int) -> OfferRevision:
    revision = db.scalar(select(OfferRevision).where(
        OfferRevision.offer_id == offer_id,
        OfferRevision.revision_number == revision_number,
    ))
    if revision is None:
        raise _error(404, "not_found", "Offer revision not found")
    return revision


def record_letter_download(db: Session, *, offer: Offer, actor: Employee | None, revision_number: int) -> None:
    _audit(db, offer, "offer.letter_downloaded", actor, revision_number=revision_number)
    db.commit()


def process_due_offer_reminders(db: Session, *, now: datetime | None = None, limit: int = 100) -> int:
    """Expire sent offers and create idempotent in-app reminders for approving HR Managers."""
    current = now or utcnow()
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Reminder processing time must be timezone-aware")
    current = current.astimezone(timezone.utc)

    # Application rows are locked first everywhere in the offer lifecycle.
    application_ids = db.scalars(
        select(Application.id)
        .join(Offer, Offer.application_id == Application.id)
        .join(OfferRevision, (OfferRevision.offer_id == Offer.id) & (OfferRevision.revision_number == Offer.current_revision_number))
        .where(
            Offer.status == OfferStatus.SENT.value,
            OfferRevision.expires_at <= current,
        )
        .order_by(Application.id)
        .limit(limit)
        .with_for_update(skip_locked=True, of=Application)
    ).all()
    expired = 0
    for application_id in application_ids:
        application = db.get(Application, application_id)
        offer = db.scalar(select(Offer).where(Offer.application_id == application_id).with_for_update())
        if application is None or offer is None or offer.status != OfferStatus.SENT.value:
            continue
        revision = current_revision(db, offer)
        if _as_utc(revision.expires_at) <= current:
            offer.status = OfferStatus.EXPIRED.value
            offer.expired_at = current
            offer.capability_revoked_at = current
            _supersede_reminders(db, offer.id)
            _audit(db, offer, "offer.expired", None, revision_number=revision.revision_number)
            expired += 1

    # Due reminder candidates are selected without locking them first; each
    # Offer's application lock serializes workers and mutations consistently.
    due_reminder_ids = db.scalars(
        select(ScheduledOfferReminder.id)
        .join(Offer, Offer.id == ScheduledOfferReminder.offer_id)
        .join(Application, Application.id == Offer.application_id)
        .where(ScheduledOfferReminder.state == "pending", ScheduledOfferReminder.scheduled_at <= current)
        .order_by(Application.id, ScheduledOfferReminder.scheduled_at, ScheduledOfferReminder.id)
        .limit(limit)
    ).all()
    delivered = 0
    for reminder_id in due_reminder_ids:
        reminder_stub = db.get(ScheduledOfferReminder, reminder_id)
        if reminder_stub is None:
            continue
        application = db.scalar(select(Application).join(Offer, Offer.application_id == Application.id).where(
            Offer.id == reminder_stub.offer_id
        ).with_for_update(skip_locked=True))
        if application is None:
            continue
        offer = db.scalar(select(Offer).where(Offer.id == reminder_stub.offer_id).with_for_update())
        reminder = db.scalar(select(ScheduledOfferReminder).where(
            ScheduledOfferReminder.id == reminder_id
        ).with_for_update())
        if offer is None or reminder is None or reminder.state != "pending":
            continue
        reminder.attempt_count += 1
        reminder.last_attempt_at = current
        revision = current_revision(db, offer)
        if (
            offer.status != OfferStatus.SENT.value
            or offer.approved_by_employee_id != reminder.recipient_employee_id
            or revision.id != reminder.offer_revision_id
            or _as_utc(revision.expires_at) != _as_utc(reminder.expires_at_snapshot)
        ):
            reminder.state = "superseded"
            reminder.last_error = None
            continue
        event_type = "offer_expiry_3d" if reminder.offset_days == 3 else "offer_expiry_1d"
        try:
            with db.begin_nested():
                db.add(Notification(
                    recipient_employee_id=reminder.recipient_employee_id,
                    event_type=event_type,
                    title="Offer approaching expiry",
                    body=f"An offer you approved expires in {reminder.offset_days} day(s).",
                    resource_type="offer",
                    resource_id=None,
                    offer_id=offer.id,
                    idempotency_key=f"offer-reminder-delivered:{reminder.id}",
                ))
                db.flush()
                reminder.state = "delivered"
                reminder.last_error = None
                delivered += 1
        except Exception:
            reminder.last_error = "persistence_failed"
    db.commit()
    return delivered + expired
