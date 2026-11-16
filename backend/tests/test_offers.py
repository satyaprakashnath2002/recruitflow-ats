from datetime import datetime, timedelta, timezone
from decimal import Decimal
import uuid

from fastapi import HTTPException
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import (
    Application,
    AuditLog,
    Candidate,
    Employee,
    EmployeeRole,
    EmployeeStatus,
    Job,
    Notification,
    Offer,
    OfferRevision,
    ScheduledOfferReminder,
)
from app.schemas.offers import OfferTermsInput
from app.services.offers import (
    approve_offer,
    create_offer,
    get_candidate_offer,
    process_due_offer_reminders,
    respond_to_offer,
    revise_offer,
    send_offer,
    submit_offer,
    withdraw_offer,
)


class MemoryMailer:
    def __init__(self):
        self.messages = []
        self.fail = False

    def send(self, recipient: str, subject: str, body: str) -> None:
        if self.fail:
            raise RuntimeError("mail unavailable")
        self.messages.append((recipient, subject, body))


@pytest.fixture
def offer_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        yield db
    Base.metadata.drop_all(engine)
    engine.dispose()


def setup_application(db: Session):
    admin = Employee(full_name="Offer Admin", email=f"offer-admin-{uuid.uuid4()}@example.test", role=EmployeeRole.ADMIN, status=EmployeeStatus.ACTIVE)
    hr = Employee(full_name="Offer HR", email=f"offer-hr-{uuid.uuid4()}@example.test", role=EmployeeRole.HR_MANAGER, status=EmployeeStatus.ACTIVE)
    recruiter = Employee(full_name="Offer Recruiter", email=f"offer-recruiter-{uuid.uuid4()}@example.test", role=EmployeeRole.RECRUITER, status=EmployeeStatus.ACTIVE)
    candidate = Candidate(full_name="Offer Candidate", email=f"offer-candidate-{uuid.uuid4()}@example.test")
    db.add_all([admin, hr, recruiter, candidate])
    db.flush()
    job = Job(
        title="Senior Engineer", department="Engineering", employment_type="Full-time", location="Remote",
        work_mode="Remote", description="Offer test", vacancies=1, status="published",
        created_by_id=admin.id, updated_by_id=admin.id,
    )
    db.add(job)
    db.flush()
    application = Application(candidate_id=candidate.id, job_id=job.id, source="referral", stage="selected")
    db.add(application)
    db.commit()
    return admin, hr, recruiter, candidate, application


def terms(expires_at: datetime | None = None) -> OfferTermsInput:
    return OfferTermsInput(
        compensation_amount=Decimal("145000.00"),
        compensation_currency="INR",
        compensation_basis="per annum",
        expires_at=expires_at or datetime.now(timezone.utc) + timedelta(days=5),
    )


def make_offer(db: Session, admin: Employee, application: Application, expiry: datetime | None = None) -> Offer:
    return create_offer(
        db,
        application_id=application.id,
        actor=admin,
        terms=terms(expiry),
        object_key=f"{uuid.uuid4()}.pdf",
        byte_size=100,
        checksum_sha256="a" * 64,
    )


def approve_and_send(db: Session, admin: Employee, hr: Employee, application: Application, expiry=None, mailer=None):
    offer = make_offer(db, admin, application, expiry)
    submit_offer(db, offer_id=offer.id, actor=admin)
    approve_offer(db, offer_id=offer.id, actor=hr)
    mailer = mailer or MemoryMailer()
    offer = send_offer(db, offer_id=offer.id, actor=hr, email_sender=mailer)
    token = mailer.messages[0][2].split("#capability=", 1)[1].splitlines()[0]
    return offer, token, mailer


def test_offer_lifecycle_scope_capability_single_response_and_stage_independence(offer_db):
    db = offer_db
    admin, hr, recruiter, candidate, application = setup_application(db)
    offer = make_offer(db, admin, application)
    with pytest.raises(HTTPException) as denied:
        approve_offer(db, offer_id=offer.id, actor=admin)
    assert denied.value.status_code == 403
    submit_offer(db, offer_id=offer.id, actor=admin)
    approve_offer(db, offer_id=offer.id, actor=hr)
    mailer = MemoryMailer()
    offer = send_offer(db, offer_id=offer.id, actor=hr, email_sender=mailer)
    assert application.stage == "offer"
    assert offer.status == "sent"
    assert mailer.messages[0][0] == candidate.email
    assert "#capability=" in mailer.messages[0][2]
    assert "145000" not in mailer.messages[0][2]
    token = mailer.messages[0][2].split("#capability=", 1)[1].splitlines()[0]
    view, revision, job_title = get_candidate_offer(db, token)
    assert view.id == offer.id and revision.compensation_amount == Decimal("145000.00")
    assert job_title == "Senior Engineer"
    accepted = respond_to_offer(db, capability=token, response="accepted")
    assert accepted.status == "accepted"
    assert application.stage == "offer"
    with pytest.raises(HTTPException) as replay:
        respond_to_offer(db, capability=token, response="rejected")
    assert replay.value.status_code == 409
    assert db.scalar(select(AuditLog).where(AuditLog.target_offer_id == offer.id, AuditLog.action == "offer.accepted"))
    assert db.scalar(select(Notification).where(Notification.offer_id == offer.id, Notification.event_type == "offer_sent"))


def test_failed_delivery_keeps_approved_state_and_stage(offer_db):
    db = offer_db
    admin, hr, _, _, application = setup_application(db)
    offer = make_offer(db, admin, application)
    submit_offer(db, offer_id=offer.id, actor=admin)
    approve_offer(db, offer_id=offer.id, actor=hr)
    mailer = MemoryMailer()
    mailer.fail = True
    with pytest.raises(HTTPException) as failed:
        send_offer(db, offer_id=offer.id, actor=hr, email_sender=mailer)
    assert failed.value.status_code == 502
    db.refresh(offer)
    db.refresh(application)
    assert offer.status == "approved"
    assert offer.capability_token_hash is None
    assert application.stage == "selected"


def test_draft_revisions_are_append_only_and_expired_or_withdrawn_offer_can_be_reissued(offer_db):
    db = offer_db
    admin, hr, _, _, application = setup_application(db)
    offer = make_offer(db, admin, application)
    original = db.scalar(select(OfferRevision).where(OfferRevision.offer_id == offer.id))
    updated = revise_offer(
        db, offer_id=offer.id, actor=admin,
        terms=terms(datetime.now(timezone.utc) + timedelta(days=8)),
        object_key=f"{uuid.uuid4()}.pdf", byte_size=110, checksum_sha256="b" * 64,
    )
    assert updated.current_revision_number == 2
    db.refresh(original)
    assert original.revision_number == 1
    with pytest.raises(ValueError, match="immutable"):
        original.compensation_amount = Decimal("1.00")
        db.flush()
    db.rollback()
    withdraw_offer(db, offer_id=offer.id, actor=hr)
    offer = make_offer(db, admin, application)
    submit_offer(db, offer_id=offer.id, actor=admin)
    approve_offer(db, offer_id=offer.id, actor=hr)
    withdraw_offer(db, offer_id=offer.id, actor=hr)
    replacement = make_offer(db, admin, application)
    assert replacement.id != offer.id


def test_expiry_reminders_are_idempotent_and_expiration_supersedes_pending(offer_db):
    db = offer_db
    admin, hr, _, _, application = setup_application(db)
    expiry = datetime.now(timezone.utc) + timedelta(days=5)
    offer, _, _ = approve_and_send(db, admin, hr, application, expiry)
    reminders = db.scalars(select(ScheduledOfferReminder).where(ScheduledOfferReminder.offer_id == offer.id)).all()
    assert {item.offset_days for item in reminders} == {1, 3}
    # Move the 3-day point past due without changing the persisted UTC schedule.
    count = process_due_offer_reminders(db, now=expiry - timedelta(days=2, hours=23))
    assert count == 1
    notification = db.scalar(select(Notification).where(Notification.offer_id == offer.id, Notification.event_type == "offer_expiry_3d"))
    assert notification is not None and notification.recipient_employee_id == hr.id
    assert process_due_offer_reminders(db, now=expiry - timedelta(days=2, hours=23)) == 0
    assert process_due_offer_reminders(db, now=expiry + timedelta(seconds=1)) == 1
    db.refresh(offer)
    assert offer.status == "expired"
    assert all(row.state in {"delivered", "superseded"} for row in reminders)


def test_active_offer_unique_constraint_blocks_concurrent_workflow_duplicates(offer_db):
    db = offer_db
    admin, _, _, _, application = setup_application(db)
    make_offer(db, admin, application)
    duplicate = Offer(application_id=application.id, created_by_employee_id=admin.id, status="draft", current_revision_number=1)
    db.add(duplicate)
    with pytest.raises(Exception):
        db.commit()
    db.rollback()


def test_offer_role_boundaries_apply_to_every_employee_mutation(offer_db):
    db = offer_db
    admin, hr, recruiter, _, application = setup_application(db)
    interviewer = Employee(
        full_name="Offer Interviewer",
        email=f"offer-interviewer-{uuid.uuid4()}@example.test",
        role=EmployeeRole.INTERVIEWER,
        status=EmployeeStatus.ACTIVE,
    )
    db.add(interviewer)
    db.commit()

    for actor in (recruiter, interviewer):
        with pytest.raises(HTTPException) as denied:
            make_offer(db, actor, application)
        assert denied.value.status_code == 403

    offer = make_offer(db, admin, application)
    submit_offer(db, offer_id=offer.id, actor=admin)
    with pytest.raises(HTTPException) as admin_approval:
        approve_offer(db, offer_id=offer.id, actor=admin)
    assert admin_approval.value.status_code == 403
    approve_offer(db, offer_id=offer.id, actor=hr)
    with pytest.raises(HTTPException) as admin_send:
        send_offer(db, offer_id=offer.id, actor=admin, email_sender=MemoryMailer())
    assert admin_send.value.status_code == 403
    with pytest.raises(HTTPException) as admin_withdraw:
        withdraw_offer(db, offer_id=offer.id, actor=admin)
    assert admin_withdraw.value.status_code == 403
