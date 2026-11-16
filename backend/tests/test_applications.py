from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
import uuid

import pytest
from pypdf import PdfWriter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core import time as company_time
from app.core.security import hash_password
from app.models.applications import Application, ApplicationNote, Candidate, StoredDocument
from app.models.auth import AuditLog, Employee, EmployeeRole, EmployeeStatus
from app.models.jobs import JobAssignment
from app.services.application_management import DuplicateApplication, create_application, ensure_job_accepts_application
from app.services.private_storage import MAX_RESUME_BYTES, InvalidResume, LocalPrivateObjectStore, OversizedResume, validate_pdf

PASSWORD = "Candidate workflow test password 77!"


def create_employee(factory: sessionmaker, email: str, role: EmployeeRole) -> Employee:
    with factory() as db:
        employee = Employee(
            full_name=email.split("@")[0], email=email, role=role, status=EmployeeStatus.ACTIVE,
            password_hash=hash_password(PASSWORD),
        )
        db.add(employee)
        db.commit()
        db.refresh(employee)
        return employee


def bearer(client, email: str) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def job_data(**changes):
    data = {
        "title": "Platform Engineer",
        "department": "Engineering",
        "employment_type": "Full-time",
        "location": "Bengaluru",
        "work_mode": "Hybrid",
        "description": "Build platform services.",
        "vacancies": 1,
        "application_deadline": (date.today() + timedelta(days=7)).isoformat(),
    }
    data.update(changes)
    return data


def create_published_job(client, admin_headers, **changes):
    response = client.post("/api/v1/jobs", json=job_data(**changes), headers=admin_headers)
    assert response.status_code == 201, response.text
    job = response.json()
    published = client.post(f"/api/v1/jobs/{job['id']}/publish", headers=admin_headers)
    assert published.status_code == 200, published.text
    return job


def pdf_bytes() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    stream = BytesIO()
    writer.write(stream)
    return stream.getvalue()


def candidate_payload(email="Applicant@Example.COM", **changes):
    data = {"full_name": "  Jamie Candidate  ", "email": email, "phone": "+1 555 0100"}
    data.update(changes)
    return data


def application_payload(job_id, email="Applicant@Example.COM", source="referral"):
    return {
        "job_id": str(job_id),
        "candidate": candidate_payload(email=email),
        "source": source,
    }


def test_candidate_normalization_global_directory_and_unique_email(api_client) -> None:
    client, factory, _ = api_client
    admin = create_employee(factory, "candidate-admin@example.com", EmployeeRole.ADMIN)
    headers = bearer(client, admin.email)
    created = client.post("/api/v1/candidates", json=candidate_payload(), headers=headers)
    assert created.status_code == 201, created.text
    assert created.json()["email"] == "applicant@example.com"
    assert created.json()["full_name"] == "Jamie Candidate"
    assert "password_hash" not in created.text
    duplicate = client.post("/api/v1/candidates", json=candidate_payload(" applicant@example.com "), headers=headers)
    assert duplicate.status_code == 409


def test_candidate_updates_are_scoped_normalized_and_audited(api_client) -> None:
    client, factory, _ = api_client
    admin = create_employee(factory, "candidate-edit-admin@example.com", EmployeeRole.ADMIN)
    recruiter = create_employee(factory, "candidate-edit-recruiter@example.com", EmployeeRole.RECRUITER)
    other = create_employee(factory, "candidate-edit-other@example.com", EmployeeRole.RECRUITER)
    admin_headers = bearer(client, admin.email)
    candidate_response = client.post("/api/v1/candidates", json=candidate_payload(), headers=admin_headers)
    candidate_id = candidate_response.json()["id"]
    job = create_published_job(client, admin_headers)
    with factory() as db:
        db.add(JobAssignment(job_id=uuid.UUID(job["id"]), employee_id=recruiter.id, assigned_by_id=admin.id))
        db.commit()
    app_result = client.post(
        "/api/v1/applications", json=application_payload(job["id"], "applicant@example.com"), headers=admin_headers
    )
    assert app_result.status_code == 201
    recruiter_headers = bearer(client, recruiter.email)
    other_headers = bearer(client, other.email)
    changed = client.patch(
        f"/api/v1/candidates/{candidate_id}", json={"email": "  NEW@Example.COM  ", "location": "Remote"}, headers=recruiter_headers
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["email"] == "new@example.com"
    assert client.patch(f"/api/v1/candidates/{candidate_id}", json={"location": "Private"}, headers=other_headers).status_code == 404
    conflict = client.patch(
        f"/api/v1/candidates/{candidate_id}", json={"email": "already@example.com"}, headers=admin_headers
    )
    assert conflict.status_code == 200
    created = client.post("/api/v1/candidates", json=candidate_payload("taken@example.com"), headers=admin_headers)
    assert created.status_code == 201
    duplicate_email = client.patch(
        f"/api/v1/candidates/{candidate_id}", json={"email": "taken@example.com"}, headers=admin_headers
    )
    assert duplicate_email.status_code == 409
    with factory() as db:
        events = db.scalars(select(AuditLog).where(AuditLog.action == "candidate.updated")).all()
        assert any(event.details == {"changed_fields": ["email", "location"]} for event in events)
        assert "new@example.com" not in str([event.details for event in events])


@pytest.mark.parametrize("role", [EmployeeRole.RECRUITER, EmployeeRole.INTERVIEWER])
def test_candidate_directory_is_manager_only(api_client, role) -> None:
    client, factory, _ = api_client
    employee = create_employee(factory, f"{role.value}-candidate@example.com", role)
    headers = bearer(client, employee.email)
    assert client.get("/api/v1/candidates", headers=headers).status_code == 403
    assert client.post("/api/v1/candidates", json=candidate_payload(), headers=headers).status_code == 403


def test_employee_application_reuses_candidate_and_prevents_duplicate(api_client) -> None:
    client, factory, _ = api_client
    admin = create_employee(factory, "application-admin@example.com", EmployeeRole.ADMIN)
    headers = bearer(client, admin.email)
    job = create_published_job(client, headers)
    first = client.post("/api/v1/applications", json=application_payload(job["id"]), headers=headers)
    assert first.status_code == 201, first.text
    assert first.json()["stage"] == "applied"
    assert first.json()["source"] == "referral"
    assert first.json()["candidate"]["email"] == "applicant@example.com"
    duplicate = client.post(
        "/api/v1/applications", json=application_payload(job["id"], " APPLICANT@example.com "), headers=headers
    )
    assert duplicate.status_code in (409, 422)
    with factory() as db:
        assert db.scalar(select(func_count(Application.id))) == 1
        assert db.scalar(select(func_count(Candidate.id))) == 1


def func_count(column):
    from sqlalchemy import func

    return func.count(column)


def test_recruiter_scope_and_candidate_through_assigned_application(api_client) -> None:
    client, factory, _ = api_client
    admin = create_employee(factory, "scope-admin@example.com", EmployeeRole.ADMIN)
    recruiter = create_employee(factory, "assigned-recruiter@example.com", EmployeeRole.RECRUITER)
    other = create_employee(factory, "other-recruiter@example.com", EmployeeRole.RECRUITER)
    admin_headers = bearer(client, admin.email)
    recruiter_headers = bearer(client, recruiter.email)
    other_headers = bearer(client, other.email)
    job = create_published_job(client, admin_headers)
    with factory() as db:
        db.add(JobAssignment(job_id=uuid.UUID(job["id"]), employee_id=recruiter.id, assigned_by_id=admin.id))
        db.commit()
    created = client.post(
        "/api/v1/applications", json=application_payload(job["id"]), headers=recruiter_headers
    )
    assert created.status_code == 201, created.text
    app_id = created.json()["id"]
    candidate_id = created.json()["candidate_id"]
    assert client.get("/api/v1/applications", headers=recruiter_headers).json()["total"] == 1
    assert client.get(f"/api/v1/applications/{app_id}", headers=other_headers).status_code == 404
    assert client.get(f"/api/v1/candidates/{candidate_id}", headers=recruiter_headers).status_code == 200
    assert client.get(f"/api/v1/candidates/{candidate_id}", headers=other_headers).status_code == 404


def test_interviewer_denied_candidate_application_and_resume_access(api_client) -> None:
    client, factory, _ = api_client
    admin = create_employee(factory, "interviewer-check-admin@example.com", EmployeeRole.ADMIN)
    interviewer = create_employee(factory, "interviewer-check@example.com", EmployeeRole.INTERVIEWER)
    admin_headers = bearer(client, admin.email)
    job = create_published_job(client, admin_headers)
    application = client.post("/api/v1/applications", json=application_payload(job["id"]), headers=admin_headers)
    app_id = application.json()["id"]
    candidate_id = application.json()["candidate_id"]
    note = client.post(f"/api/v1/applications/{app_id}/notes", json={"body": "Internal"}, headers=admin_headers)
    resume = client.post(
        f"/api/v1/applications/{app_id}/resume",
        files={"resume": ("resume.pdf", pdf_bytes(), "application/pdf")},
        headers=admin_headers,
    )
    assert note.status_code == 201 and resume.status_code == 201
    interviewer_headers = bearer(client, interviewer.email)
    assert client.get("/api/v1/applications", headers=interviewer_headers).status_code == 403
    assert client.get(f"/api/v1/applications/{app_id}", headers=interviewer_headers).status_code == 403
    assert client.get(f"/api/v1/candidates/{candidate_id}", headers=interviewer_headers).status_code == 403
    assert client.get(f"/api/v1/applications/{app_id}/notes", headers=interviewer_headers).status_code == 403
    assert client.get(f"/api/v1/documents/{resume.json()['id']}", headers=interviewer_headers).status_code == 403


def test_public_application_privacy_resume_and_duplicate_generic_response(api_client, tmp_path: Path) -> None:
    client, factory, _ = api_client
    admin = create_employee(factory, "public-flow-admin@example.com", EmployeeRole.ADMIN)
    admin_headers = bearer(client, admin.email)
    job = create_published_job(client, admin_headers)
    content = pdf_bytes()
    files = {"resume": ("resume.pdf", content, "application/pdf")}
    data = {"full_name": "Public Applicant", "email": "Public@Example.com"}
    first = client.post(f"/api/v1/public/jobs/{job['id']}/applications", data=data, files=files)
    second = client.post(f"/api/v1/public/jobs/{job['id']}/applications", data=data, files=files)
    assert first.status_code == 202 and second.status_code == 202
    assert first.json() == second.json()
    assert "id" not in first.json()
    assert "candidate" not in first.text.lower() and "application_id" not in first.text.lower()
    with factory() as db:
        candidate = db.scalar(select(Candidate).where(Candidate.email == "public@example.com"))
        application = db.scalar(select(Application).where(Application.job_id == uuid.UUID(job["id"])))
        document = db.scalar(select(StoredDocument).where(StoredDocument.application_id == application.id))
        assert candidate is not None and application is not None and document is not None
        assert len(db.scalars(select(Application)).all()) == 1
        assert len(db.scalars(select(StoredDocument)).all()) == 1
        doc_id = document.id
        public_events = db.scalars(select(AuditLog).where(AuditLog.actor_kind == "public")).all()
        assert {event.action for event in public_events} == {"candidate.created", "application.created", "resume.uploaded"}
        assert all(event.actor_employee_id is None for event in public_events)
        assert "public@example.com" not in str([event.details for event in public_events])
    storage = LocalPrivateObjectStore(tmp_path / "private-documents")
    assert list((tmp_path / "private-documents").glob("*.pdf"))
    assert client.get(f"/api/v1/documents/{doc_id}").status_code == 401
    downloaded = client.get(f"/api/v1/documents/{doc_id}", headers=admin_headers)
    assert downloaded.status_code == 200
    assert downloaded.content == content
    assert "private, no-store" in downloaded.headers["cache-control"]
    with factory() as db:
        assert db.scalar(select(AuditLog).where(AuditLog.action == "resume.downloaded")) is not None


def test_public_duplicate_and_new_candidate_storage_errors_are_indistinguishable(api_client) -> None:
    from fastapi.testclient import TestClient

    from app.api.v1.applications import get_private_object_store

    client, factory, _ = api_client
    admin = create_employee(factory, "public-storage-error-admin@example.com", EmployeeRole.ADMIN)
    admin_headers = bearer(client, admin.email)
    job = create_published_job(client, admin_headers)
    existing_data = {"full_name": "Existing Person", "email": "existing-person@example.com"}
    created = client.post(
        f"/api/v1/public/jobs/{job['id']}/applications",
        data=existing_data,
        files={"resume": ("resume.pdf", pdf_bytes(), "application/pdf")},
    )
    assert created.status_code == 202

    class FailingStore:
        def put(self, content: bytes) -> str:
            raise OSError("storage unavailable")

        def read(self, key: str) -> bytes:
            raise OSError("storage unavailable")

        def delete(self, key: str) -> None:
            pass

    client.app.dependency_overrides[get_private_object_store] = lambda: FailingStore()
    try:
        with TestClient(client.app, raise_server_exceptions=False) as public_client:
            duplicate = public_client.post(
                f"/api/v1/public/jobs/{job['id']}/applications",
                data=existing_data,
                files={"resume": ("resume.pdf", pdf_bytes(), "application/pdf")},
            )
            new_candidate = public_client.post(
                f"/api/v1/public/jobs/{job['id']}/applications",
                data={"full_name": "New Person", "email": "new-person@example.com"},
                files={"resume": ("resume.pdf", pdf_bytes(), "application/pdf")},
            )
    finally:
        client.app.dependency_overrides.pop(get_private_object_store, None)

    assert duplicate.status_code == new_candidate.status_code == 500
    assert duplicate.json() == new_candidate.json()
    assert "existing-person@example.com" not in duplicate.text
    assert "new-person@example.com" not in new_candidate.text


def test_pdf_validation_checks_content_and_size() -> None:
    content = pdf_bytes()
    checksum = validate_pdf(content)
    assert len(checksum) == 64
    with pytest.raises(InvalidResume):
        validate_pdf(b"not a PDF even when named resume.pdf")
    with pytest.raises(OversizedResume):
        validate_pdf(b"x" * (MAX_RESUME_BYTES + 1))
    at_limit = content + (b" " * (MAX_RESUME_BYTES - len(content)))
    assert len(at_limit) == MAX_RESUME_BYTES
    assert len(validate_pdf(at_limit)) == 64


def test_resume_upload_replacement_scope_and_no_public_url(api_client) -> None:
    client, factory, _ = api_client
    admin = create_employee(factory, "resume-admin@example.com", EmployeeRole.ADMIN)
    unassigned = create_employee(factory, "resume-other@example.com", EmployeeRole.RECRUITER)
    admin_headers = bearer(client, admin.email)
    job = create_published_job(client, admin_headers)
    app_resp = client.post("/api/v1/applications", json=application_payload(job["id"]), headers=admin_headers)
    app_id = app_resp.json()["id"]
    body = pdf_bytes()
    uploaded = client.post(
        f"/api/v1/applications/{app_id}/resume", files={"resume": ("fake.txt", body, "text/plain")}, headers=admin_headers
    )
    assert uploaded.status_code == 201, uploaded.text
    assert "url" not in uploaded.json() and "object_key" not in uploaded.json()
    replaced = client.put(
        f"/api/v1/applications/{app_id}/resume", files={"resume": ("resume.pdf", body, "application/pdf")}, headers=admin_headers
    )
    assert replaced.status_code == 200
    assert client.get(f"/api/v1/documents/{uploaded.json()['id']}", headers=bearer(client, unassigned.email)).status_code == 404
    with factory() as db:
        assert len(db.scalars(select(StoredDocument)).all()) == 2
        actions = set(db.scalars(select(AuditLog.action).where(AuditLog.action.like("resume.%"))).all())
        assert {"resume.uploaded", "resume.replaced"} <= actions


def test_resume_upload_rejects_invalid_pdf_and_too_large(api_client) -> None:
    client, factory, _ = api_client
    admin = create_employee(factory, "invalid-resume-admin@example.com", EmployeeRole.ADMIN)
    headers = bearer(client, admin.email)
    job = create_published_job(client, headers)
    app_id = client.post("/api/v1/applications", json=application_payload(job["id"]), headers=headers).json()["id"]
    invalid = client.post(
        f"/api/v1/applications/{app_id}/resume", files={"resume": ("resume.pdf", b"not pdf", "application/pdf")}, headers=headers
    )
    assert invalid.status_code == 415
    oversized = client.post(
        f"/api/v1/applications/{app_id}/resume",
        files={"resume": ("resume.pdf", b"x" * (MAX_RESUME_BYTES + 1), "application/pdf")}, headers=headers,
    )
    assert oversized.status_code == 413


def test_resume_storage_failure_does_not_create_document_or_audit(api_client) -> None:
    from app.api.v1.applications import get_private_object_store

    client, factory, _ = api_client
    admin = create_employee(factory, "storage-failure-admin@example.com", EmployeeRole.ADMIN)
    headers = bearer(client, admin.email)
    job = create_published_job(client, headers)
    app_id = client.post("/api/v1/applications", json=application_payload(job["id"]), headers=headers).json()["id"]

    class FailingStore:
        def put(self, content: bytes) -> str:
            raise OSError("storage unavailable")

        def read(self, key: str) -> bytes:
            raise OSError("storage unavailable")

        def delete(self, key: str) -> None:
            pass

    client.app.dependency_overrides[get_private_object_store] = lambda: FailingStore()
    try:
        with pytest.raises(OSError, match="storage unavailable"):
            client.post(
                f"/api/v1/applications/{app_id}/resume",
                files={"resume": ("resume.pdf", pdf_bytes(), "application/pdf")},
                headers=headers,
            )
    finally:
        client.app.dependency_overrides.pop(get_private_object_store, None)

    with factory() as db:
        assert db.scalar(select(StoredDocument.id)) is None
        assert db.scalar(select(AuditLog.id).where(AuditLog.action == "resume.uploaded")) is None


def test_failed_resume_replacement_removes_new_object_and_preserves_old_document(api_client, tmp_path: Path) -> None:
    from sqlalchemy import event

    from app.api.v1.applications import get_private_object_store

    client, factory, _ = api_client
    admin = create_employee(factory, "replacement-failure-admin@example.com", EmployeeRole.ADMIN)
    headers = bearer(client, admin.email)
    job = create_published_job(client, headers)
    app_id = client.post("/api/v1/applications", json=application_payload(job["id"]), headers=headers).json()["id"]
    original = pdf_bytes()
    first = client.post(
        f"/api/v1/applications/{app_id}/resume",
        files={"resume": ("resume.pdf", original, "application/pdf")},
        headers=headers,
    )
    assert first.status_code == 201
    original_store = client.app.dependency_overrides[get_private_object_store]()
    with factory() as db:
        original_document = db.get(StoredDocument, uuid.UUID(first.json()["id"]))
        original_key = original_document.object_key

    class TrackingStore(LocalPrivateObjectStore):
        def __init__(self, root: Path) -> None:
            super().__init__(root)
            self.last_key: str | None = None

        def put(self, content: bytes) -> str:
            self.last_key = super().put(content)
            return self.last_key

    replacement_store = TrackingStore(tmp_path / "replacement-store")
    client.app.dependency_overrides[get_private_object_store] = lambda: replacement_store
    engine = factory.kw["bind"]

    def reject_document_insert(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT INTO STORED_DOCUMENTS"):
            raise RuntimeError("simulated metadata insert failure")

    event.listen(engine, "before_cursor_execute", reject_document_insert)
    try:
        with pytest.raises(RuntimeError, match="simulated metadata insert failure"):
            client.put(
                f"/api/v1/applications/{app_id}/resume",
                files={"resume": ("replacement.pdf", pdf_bytes(), "application/pdf")},
                headers=headers,
            )
    finally:
        event.remove(engine, "before_cursor_execute", reject_document_insert)
        client.app.dependency_overrides.pop(get_private_object_store, None)

    assert replacement_store.last_key is not None
    assert not replacement_store._path(replacement_store.last_key).exists()
    assert original_store.read(original_key) == original
    with factory() as db:
        assert len(db.scalars(select(StoredDocument)).all()) == 1


def test_local_private_storage_rejects_path_traversal(tmp_path: Path) -> None:
    storage = LocalPrivateObjectStore(tmp_path / "private")
    for key in ("../secret.pdf", "..\\secret.pdf", "/etc/passwd", str(uuid.uuid4()) + "/../../x.pdf"):
        with pytest.raises(ValueError):
            storage.read(key)


def test_note_permissions_and_append_only(api_client) -> None:
    client, factory, _ = api_client
    admin = create_employee(factory, "note-admin@example.com", EmployeeRole.ADMIN)
    hr = create_employee(factory, "note-hr@example.com", EmployeeRole.HR_MANAGER)
    recruiter = create_employee(factory, "note-recruiter@example.com", EmployeeRole.RECRUITER)
    interviewer = create_employee(factory, "note-interviewer@example.com", EmployeeRole.INTERVIEWER)
    admin_headers = bearer(client, admin.email)
    job = create_published_job(client, admin_headers)
    with factory() as db:
        db.add(JobAssignment(job_id=uuid.UUID(job["id"]), employee_id=recruiter.id, assigned_by_id=admin.id))
        db.commit()
    app_id = client.post("/api/v1/applications", json=application_payload(job["id"]), headers=admin_headers).json()["id"]
    recruiter_headers = bearer(client, recruiter.email)
    created = client.post(f"/api/v1/applications/{app_id}/notes", json={"body": "Recruiter note"}, headers=recruiter_headers)
    assert created.status_code == 201
    assert client.get(f"/api/v1/applications/{app_id}/notes", headers=bearer(client, hr.email)).status_code == 200
    assert client.get(f"/api/v1/applications/{app_id}/notes", headers=bearer(client, interviewer.email)).status_code == 403
    assert client.patch(f"/api/v1/applications/{app_id}/notes/{created.json()['id']}", json={"body": "changed"}, headers=admin_headers).status_code == 404
    with factory() as db:
        note = db.get(ApplicationNote, uuid.UUID(created.json()["id"]))
        note.body = "tampered"
        with pytest.raises(ValueError, match="append-only"):
            db.commit()
        db.rollback()
        event = db.scalar(select(AuditLog).where(AuditLog.action == "application.note_created"))
        assert event is not None and event.target_application_id == uuid.UUID(app_id)


def test_stage_transition_matrix_actor_rules_and_audit(api_client) -> None:
    client, factory, _ = api_client
    admin = create_employee(factory, "stage-admin@example.com", EmployeeRole.ADMIN)
    hr = create_employee(factory, "stage-hr@example.com", EmployeeRole.HR_MANAGER)
    recruiter = create_employee(factory, "stage-recruiter@example.com", EmployeeRole.RECRUITER)
    interviewer = create_employee(factory, "stage-interviewer@example.com", EmployeeRole.INTERVIEWER)
    admin_headers = bearer(client, admin.email)
    job = create_published_job(client, admin_headers)
    with factory() as db:
        db.add(JobAssignment(job_id=uuid.UUID(job["id"]), employee_id=recruiter.id, assigned_by_id=admin.id))
        db.commit()
    app_id = client.post("/api/v1/applications", json=application_payload(job["id"]), headers=admin_headers).json()["id"]
    recruiter_headers = bearer(client, recruiter.email)
    hr_headers = bearer(client, hr.email)
    interviewer_headers = bearer(client, interviewer.email)

    assert client.post(f"/api/v1/applications/{app_id}/stage", json={"stage": "shortlisted"}, headers=admin_headers).status_code == 400
    assert client.post(f"/api/v1/applications/{app_id}/stage", json={"stage": "screening"}, headers=admin_headers).status_code == 200
    assert client.post(f"/api/v1/applications/{app_id}/stage", json={"stage": "shortlisted"}, headers=recruiter_headers).status_code == 200
    assert client.post(f"/api/v1/applications/{app_id}/stage", json={"stage": "interview"}, headers=recruiter_headers).status_code == 200
    assert client.post(f"/api/v1/applications/{app_id}/stage", json={"stage": "selected"}, headers=recruiter_headers).status_code == 400
    assert client.post(f"/api/v1/applications/{app_id}/stage", json={"stage": "selected"}, headers=admin_headers).status_code == 400
    assert client.post(f"/api/v1/applications/{app_id}/stage", json={"stage": "selected"}, headers=interviewer_headers).status_code == 403
    assert client.post(f"/api/v1/applications/{app_id}/stage", json={"stage": "selected"}, headers=hr_headers).status_code == 200
    assert client.patch(f"/api/v1/applications/{app_id}", json={"stage": "offer"}, headers=hr_headers).status_code == 422
    assert client.post(f"/api/v1/applications/{app_id}/stage", json={"stage": "offer"}, headers=hr_headers).status_code == 200
    assert client.post(f"/api/v1/applications/{app_id}/stage", json={"stage": "hired"}, headers=hr_headers).status_code == 200
    assert client.post(f"/api/v1/applications/{app_id}/stage", json={"stage": "rejected"}, headers=hr_headers).status_code == 400
    with factory() as db:
        stages = db.scalars(select(AuditLog).where(AuditLog.action == "application.stage_changed")).all()
        assert [(event.details["from"], event.details["to"]) for event in stages] == [
            ("applied", "screening"), ("screening", "shortlisted"), ("shortlisted", "interview"),
            ("interview", "selected"), ("selected", "offer"), ("offer", "hired"),
        ]
        assert all(event.actor_employee_id is not None for event in stages)


def test_rejected_and_withdrawn_from_nonterminal_only(api_client) -> None:
    client, factory, _ = api_client
    hr = create_employee(factory, "outcome-hr@example.com", EmployeeRole.HR_MANAGER)
    headers = bearer(client, hr.email)
    job = create_published_job(client, headers)
    first = client.post("/api/v1/applications", json=application_payload(job["id"], "one@example.com"), headers=headers).json()
    second = client.post("/api/v1/applications", json=application_payload(job["id"], "two@example.com"), headers=headers).json()
    assert client.post(f"/api/v1/applications/{first['id']}/stage", json={"stage": "rejected"}, headers=headers).status_code == 200
    assert client.post(f"/api/v1/applications/{second['id']}/stage", json={"stage": "withdrawn"}, headers=headers).status_code == 200


@pytest.mark.parametrize("from_stage", ["applied", "screening", "shortlisted", "interview", "selected", "offer"])
@pytest.mark.parametrize("outcome", ["rejected", "withdrawn"])
def test_hr_manager_can_reject_or_withdraw_from_each_nonterminal_stage(api_client, from_stage, outcome) -> None:
    client, factory, _ = api_client
    hr = create_employee(factory, f"outcome-{from_stage}-{outcome}@example.com", EmployeeRole.HR_MANAGER)
    headers = bearer(client, hr.email)
    job = create_published_job(client, headers)
    app_id = client.post(
        "/api/v1/applications", json=application_payload(job["id"], f"{from_stage}-{outcome}@example.com"), headers=headers
    ).json()["id"]
    path = ["applied", "screening", "shortlisted", "interview", "selected", "offer"]
    for stage in path[1 : path.index(from_stage) + 1]:
        result = client.post(f"/api/v1/applications/{app_id}/stage", json={"stage": stage}, headers=headers)
        assert result.status_code == 200, result.text
    result = client.post(f"/api/v1/applications/{app_id}/stage", json={"stage": outcome}, headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()["stage"] == outcome


@pytest.mark.parametrize("terminal", ["hired", "rejected", "withdrawn"])
def test_all_terminal_stages_reject_further_transitions(api_client, terminal) -> None:
    client, factory, _ = api_client
    hr = create_employee(factory, f"terminal-{terminal}@example.com", EmployeeRole.HR_MANAGER)
    headers = bearer(client, hr.email)
    job = create_published_job(client, headers)
    app_id = client.post(
        "/api/v1/applications", json=application_payload(job["id"], f"terminal-{terminal}@example.com"), headers=headers
    ).json()["id"]
    final_path = {
        "hired": ["screening", "shortlisted", "interview", "selected", "offer", "hired"],
        "rejected": ["rejected"],
        "withdrawn": ["withdrawn"],
    }[terminal]
    for stage in final_path:
        result = client.post(f"/api/v1/applications/{app_id}/stage", json={"stage": stage}, headers=headers)
        assert result.status_code == 200, result.text
    rejected = client.post(f"/api/v1/applications/{app_id}/stage", json={"stage": "screening"}, headers=headers)
    assert rejected.status_code == 400


def test_public_deadline_and_no_deadline_policy(api_client, monkeypatch) -> None:
    client, factory, _ = api_client
    admin = create_employee(factory, "deadline-admin@example.com", EmployeeRole.ADMIN)
    headers = bearer(client, admin.email)
    # Freeze the shared UTC clock before job creation: India is still Nov 6,
    # so the Nov 7 deadline passes JobCreate validation as a future date.
    monkeypatch.setattr(
        company_time,
        "utcnow",
        lambda: datetime(2026, 11, 6, 18, 29, 59, tzinfo=timezone.utc),
    )
    no_deadline = create_published_job(client, headers, application_deadline=None)
    deadline_job = create_published_job(client, headers, title="Deadline Position", application_deadline="2026-11-07")
    # 18:29:59 UTC is 23:59:59 in Asia/Kolkata on the deadline date.
    monkeypatch.setattr(
        company_time,
        "utcnow",
        lambda: datetime(2026, 11, 7, 18, 29, 59, tzinfo=timezone.utc),
    )
    content = pdf_bytes()
    no_deadline_response = client.post(
        f"/api/v1/public/jobs/{no_deadline['id']}/applications",
        data={"full_name": "No Deadline", "email": "nodate@example.com"},
        files={"resume": ("resume.pdf", content, "application/pdf")},
    )
    assert no_deadline_response.status_code == 202
    accepted = client.post(
        f"/api/v1/public/jobs/{deadline_job['id']}/applications",
        data={"full_name": "Deadline Day", "email": "deadlineday@example.com"},
        files={"resume": ("resume.pdf", content, "application/pdf")},
    )
    assert accepted.status_code == 202
    # The next UTC second is midnight on Nov 8 in Asia/Kolkata.
    monkeypatch.setattr(
        company_time,
        "utcnow",
        lambda: datetime(2026, 11, 7, 18, 30, 0, tzinfo=timezone.utc),
    )
    rejected = client.post(
        f"/api/v1/public/jobs/{deadline_job['id']}/applications",
        data={"full_name": "Late Applicant", "email": "late@example.com"},
        files={"resume": ("resume.pdf", content, "application/pdf")},
    )
    assert rejected.status_code == 400
    no_deadline_later = client.post(
        f"/api/v1/public/jobs/{no_deadline['id']}/applications",
        data={"full_name": "Long Open Role", "email": "longopen@example.com"},
        files={"resume": ("resume.pdf", content, "application/pdf")},
    )
    assert no_deadline_later.status_code == 202


def test_employee_post_deadline_application_audited(api_client, monkeypatch) -> None:
    client, factory, _ = api_client
    admin = create_employee(factory, "postdeadline-admin@example.com", EmployeeRole.ADMIN)
    headers = bearer(client, admin.email)
    monkeypatch.setattr(
        company_time,
        "utcnow",
        lambda: datetime(2026, 11, 6, 18, 29, 59, tzinfo=timezone.utc),
    )
    job = create_published_job(client, headers, application_deadline="2026-11-07")
    monkeypatch.setattr(
        company_time,
        "utcnow",
        lambda: datetime(2026, 11, 7, 18, 30, 0, tzinfo=timezone.utc),
    )
    created = client.post("/api/v1/applications", json=application_payload(job["id"]), headers=headers)
    assert created.status_code == 201, created.text
    with factory() as db:
        event = db.scalar(select(AuditLog).where(AuditLog.action == "application.created_after_deadline"))
        assert event is not None and event.details == {"job_deadline": "2026-11-07"}


def test_controlled_application_patch_never_allows_unspecified_fields(api_client) -> None:
    client, factory, _ = api_client
    admin = create_employee(factory, "patch-admin@example.com", EmployeeRole.ADMIN)
    headers = bearer(client, admin.email)
    job = create_published_job(client, headers)
    app_id = client.post("/api/v1/applications", json=application_payload(job["id"]), headers=headers).json()["id"]
    hr = create_employee(factory, "patch-hr@example.com", EmployeeRole.HR_MANAGER)
    recruiter = create_employee(factory, "patch-recruiter@example.com", EmployeeRole.RECRUITER)
    with factory() as db:
        db.add(JobAssignment(job_id=uuid.UUID(job["id"]), employee_id=recruiter.id, assigned_by_id=admin.id))
        db.commit()
    for authorized in (admin, hr, recruiter):
        result = client.patch(f"/api/v1/applications/{app_id}", json={}, headers=bearer(client, authorized.email))
        assert result.status_code == 422
        assert result.json()["error"]["status"] == "no_editable_fields"
    for field, value in (("candidate_id", str(uuid.uuid4())), ("job_id", str(uuid.uuid4())), ("source", "recruiter"), ("stage", "screening"), ("unexpected", "value")):
        response = client.patch(f"/api/v1/applications/{app_id}", json={field: value}, headers=headers)
        assert response.status_code == 422
    interviewer = create_employee(factory, "patch-interviewer@example.com", EmployeeRole.INTERVIEWER)
    denied = client.patch(f"/api/v1/applications/{app_id}", json={}, headers=bearer(client, interviewer.email))
    assert denied.status_code == 403


def test_application_unique_pair_database_constraint(api_client) -> None:
    # A database unique constraint is the final arbiter for simultaneous submissions.
    client, factory, _ = api_client
    admin = create_employee(factory, "constraint-admin@example.com", EmployeeRole.ADMIN)
    headers = bearer(client, admin.email)
    job = create_published_job(client, headers)
    created = client.post("/api/v1/applications", json=application_payload(job["id"]), headers=headers)
    assert created.status_code == 201
    with factory() as db:
        duplicate = Application(
            candidate_id=uuid.UUID(created.json()["candidate_id"]),
            job_id=uuid.UUID(job["id"]),
            source="referral",
            stage="applied",
        )
        db.add(duplicate)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_company_today_uses_asia_kolkata() -> None:
    from app.services.application_management import company_today

    assert company_today(now=datetime(2026, 11, 5, 19, 0, tzinfo=timezone.utc)).isoformat() == "2026-11-06"

