"""HTTP integration coverage for the approved Offer routes and capability boundary."""

from datetime import datetime, timedelta, timezone
from io import BytesIO
import re
import uuid

from pypdf import PdfWriter
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.security import hash_password
from app.models.applications import Application, Candidate
from app.models.auth import AuditLog, Employee, EmployeeRole, EmployeeStatus
from app.models.jobs import Job

PASSWORD = "Offer API integration test password 77!"


def _login(client, email: str) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _pdf_bytes() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    stream = BytesIO()
    writer.write(stream)
    return stream.getvalue()


def _employee(factory: sessionmaker, role: EmployeeRole, label: str) -> Employee:
    employee = Employee(
        full_name=label,
        email=f"{label.lower().replace(' ', '-')}-{uuid.uuid4().hex}@example.com",
        role=role,
        status=EmployeeStatus.ACTIVE,
        password_hash=hash_password(PASSWORD),
    )
    with factory.begin() as db:
        db.add(employee)
    return employee


def test_offer_http_workflow_authorization_capability_privacy_and_audit(api_client) -> None:
    client, factory, mailer = api_client
    admin = _employee(factory, EmployeeRole.ADMIN, "Offer API Admin")
    hr = _employee(factory, EmployeeRole.HR_MANAGER, "Offer API HR")
    recruiter = _employee(factory, EmployeeRole.RECRUITER, "Offer API Recruiter")
    candidate = Candidate(full_name="Private Offer Candidate", email=f"candidate-{uuid.uuid4().hex}@example.com")
    with factory.begin() as db:
        db.add(candidate)
        db.flush()
        job = Job(
            title="Offer API Engineer",
            department="Engineering",
            employment_type="Full-time",
            location="Bengaluru",
            work_mode="Hybrid",
            description="Disposable API integration job",
            vacancies=1,
            status="published",
            created_by_id=admin.id,
            updated_by_id=admin.id,
        )
        db.add(job)
        db.flush()
        application = Application(candidate_id=candidate.id, job_id=job.id, source="referral", stage="selected")
        db.add(application)
        db.flush()
        application_id = application.id

    admin_headers = _login(client, admin.email)
    hr_headers = _login(client, hr.email)
    recruiter_headers = _login(client, recruiter.email)
    terms = {
        "compensation_amount": "145000.00",
        "compensation_currency": "INR",
        "compensation_basis": "per annum",
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
    }
    letter = _pdf_bytes()
    create = client.post(
        f"/api/v1/applications/{application_id}/offers",
        data=terms,
        files={"letter": ("offer.pdf", letter, "application/pdf")},
        headers=admin_headers,
    )
    assert create.status_code == 201, create.text
    offer = create.json()
    offer_id = offer["id"]
    assert offer["status"] == "draft"
    assert offer["current_revision_number"] == 1
    assert "candidate" not in str(offer).lower()

    assert client.get("/api/v1/offers", headers=recruiter_headers).status_code == 403
    assert client.get("/api/v1/offers", headers=admin_headers).json()["total"] == 1
    employee_letter = client.get(
        f"/api/v1/offers/{offer_id}/revisions/1/letter", headers=admin_headers
    )
    assert employee_letter.status_code == 200
    assert employee_letter.content == letter
    assert employee_letter.headers["cache-control"] == "private, no-store"

    revised = client.post(
        f"/api/v1/offers/{offer_id}/revisions",
        data={**terms, "compensation_amount": "150000.00"},
        files={"letter": ("offer-revision.pdf", letter, "application/pdf")},
        headers=admin_headers,
    )
    assert revised.status_code == 200, revised.text
    assert revised.json()["current_revision_number"] == 2
    revisions = client.get(f"/api/v1/offers/{offer_id}/revisions", headers=admin_headers)
    assert revisions.status_code == 200
    assert [row["revision_number"] for row in revisions.json()["items"]] == [1, 2]

    submitted = client.post(f"/api/v1/offers/{offer_id}/submit", headers=admin_headers)
    assert submitted.status_code == 200 and submitted.json()["status"] == "pending_approval"
    assert client.post(f"/api/v1/offers/{offer_id}/approve", headers=admin_headers).status_code == 403
    approved = client.post(f"/api/v1/offers/{offer_id}/approve", headers=hr_headers)
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    sent = client.post(f"/api/v1/offers/{offer_id}/send", headers=hr_headers)
    assert sent.status_code == 200 and sent.json()["status"] == "sent"
    assert sent.json()["current_revision_number"] == 2
    assert len(mailer.messages) == 1
    assert mailer.messages[0][0] == candidate.email
    capability_match = re.search(r"#capability=([^\s]+)", mailer.messages[0][2])
    assert capability_match is not None
    capability = capability_match.group(1)
    assert "150000" not in mailer.messages[0][2]

    public_headers = {"X-Offer-Capability": capability}
    unauthorized = client.get("/api/v1/public/offers/current")
    assert unauthorized.status_code == 422
    public_offer = client.get("/api/v1/public/offers/current", headers=public_headers)
    assert public_offer.status_code == 200
    assert public_offer.headers["cache-control"] == "private, no-store"
    assert set(public_offer.json()) == {
        "job_title", "compensation_amount", "compensation_currency", "compensation_basis", "expires_at", "response"
    }
    assert public_offer.json()["job_title"] == "Offer API Engineer"
    assert candidate.email not in public_offer.text
    public_letter = client.get("/api/v1/public/offers/letter", headers=public_headers)
    assert public_letter.status_code == 200 and public_letter.content == letter
    assert public_letter.headers["cache-control"] == "private, no-store"

    response = client.post(
        "/api/v1/public/offers/response", json={"response": "accepted"}, headers=public_headers
    )
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    replay = client.post(
        "/api/v1/public/offers/response", json={"response": "rejected"}, headers=public_headers
    )
    assert replay.status_code == 409

    with factory() as db:
        events = set(
            db.scalars(select(AuditLog.action).where(AuditLog.target_offer_id == uuid.UUID(offer_id))).all()
        )
        assert {
            "offer.created", "offer.revised", "offer.submitted", "offer.approved", "offer.sent",
            "offer.accepted", "offer.letter_downloaded",
        } <= events
        persisted_application = db.get(Application, application_id)
        assert persisted_application is not None and persisted_application.stage == "offer"
