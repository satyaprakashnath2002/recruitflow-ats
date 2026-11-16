import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.security import hash_password
from app.models.auth import AuditLog, Employee, EmployeeRole, EmployeeStatus, RefreshSession

PASSWORD = "Employee directory password 77!"


def create_employee(factory: sessionmaker, email: str, role: EmployeeRole, *, name: str | None = None) -> Employee:
    with factory() as db:
        employee = Employee(
            full_name=name,
            email=email,
            role=role,
            status=EmployeeStatus.ACTIVE,
            password_hash=hash_password(PASSWORD),
        )
        db.add(employee)
        db.commit()
        db.refresh(employee)
        return employee


def bearer(client, email: str) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_admin_employee_directory_search_filters_pagination_and_empty(api_client) -> None:
    client, factory, _ = api_client
    admin = create_employee(factory, "admin-directory@example.com", EmployeeRole.ADMIN, name="Alex Admin")
    create_employee(factory, "sam.recruiter@example.com", EmployeeRole.RECRUITER, name="Sam Rivera")
    create_employee(factory, "lee.hr@example.com", EmployeeRole.HR_MANAGER, name="Lee Harper")
    create_employee(factory, "pat.interviewer@example.com", EmployeeRole.INTERVIEWER, name="Pat Kim")
    headers = bearer(client, admin.email)

    directory = client.get("/api/v1/employees", headers=headers)
    assert directory.status_code == 200
    assert directory.json()["total"] == 4
    assert set(directory.json()["items"][0]) == {
        "id", "full_name", "email", "role", "status", "created_at", "updated_at", "last_login_at"
    }
    assert all("password_hash" not in row and "token_hash" not in row for row in directory.json()["items"])

    by_name = client.get("/api/v1/employees?search=rivera", headers=headers)
    assert by_name.json()["total"] == 1
    assert by_name.json()["items"][0]["email"] == "sam.recruiter@example.com"
    by_email = client.get("/api/v1/employees?search=LEE.HR", headers=headers)
    assert by_email.json()["items"][0]["full_name"] == "Lee Harper"
    filtered = client.get("/api/v1/employees?role=interviewer&status=active", headers=headers)
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["role"] == "interviewer"
    page = client.get("/api/v1/employees?page=2&page_size=2", headers=headers)
    assert page.json()["page"] == 2 and page.json()["page_size"] == 2
    assert len(page.json()["items"]) == 2
    empty = client.get("/api/v1/employees?search=no-such-person", headers=headers)
    assert empty.json() == {"items": [], "page": 1, "page_size": 20, "total": 0}


@pytest.mark.parametrize("role", [EmployeeRole.HR_MANAGER, EmployeeRole.RECRUITER, EmployeeRole.INTERVIEWER])
def test_non_admin_cannot_read_employee_directory(api_client, role: EmployeeRole) -> None:
    client, factory, _ = api_client
    employee = create_employee(factory, f"{role.value}-directory@example.com", role)
    headers = bearer(client, employee.email)
    assert client.get("/api/v1/employees", headers=headers).status_code == 403
    assert client.get(f"/api/v1/employees/{employee.id}", headers=headers).status_code == 403
    assert client.patch(
        f"/api/v1/employees/{employee.id}", json={"full_name": "Changed Name"}, headers=headers
    ).status_code == 403


def test_directory_requires_authentication_and_valid_pagination(api_client) -> None:
    client, factory, _ = api_client
    employee = create_employee(factory, "directory-admin@example.com", EmployeeRole.ADMIN)
    assert client.get("/api/v1/employees").status_code == 401
    headers = bearer(client, employee.email)
    assert client.get("/api/v1/employees?page=0", headers=headers).status_code == 422
    assert client.get("/api/v1/employees?page_size=101", headers=headers).status_code == 422


def test_employee_detail_existing_missing_and_invalid_ids(api_client) -> None:
    client, factory, _ = api_client
    admin = create_employee(factory, "detail-admin@example.com", EmployeeRole.ADMIN)
    target = create_employee(factory, "detail-target@example.com", EmployeeRole.RECRUITER, name="Target Person")
    headers = bearer(client, admin.email)

    found = client.get(f"/api/v1/employees/{target.id}", headers=headers)
    assert found.status_code == 200 and found.json()["full_name"] == "Target Person"
    assert client.get(f"/api/v1/employees/{uuid.uuid4()}", headers=headers).status_code == 404
    assert client.get("/api/v1/employees/not-a-uuid", headers=headers).status_code == 422


def test_admin_can_update_name_and_change_role_with_audit(api_client) -> None:
    client, factory, _ = api_client
    admin = create_employee(factory, "change-admin@example.com", EmployeeRole.ADMIN)
    target = create_employee(factory, "change-target@example.com", EmployeeRole.RECRUITER, name="Before Name")
    headers = bearer(client, admin.email)

    response = client.patch(
        f"/api/v1/employees/{target.id}",
        json={"full_name": "After Name", "role": "hr_manager"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["full_name"] == "After Name"
    assert response.json()["role"] == "hr_manager"
    with factory() as db:
        events = db.scalars(select(AuditLog).where(AuditLog.target_employee_id == target.id).order_by(AuditLog.action)).all()
        assert {event.action for event in events} == {"employee.updated", "employee.role_changed"}
        assert all(event.actor_employee_id == admin.id for event in events)
        name_event = next(event for event in events if event.action == "employee.updated")
        assert name_event.details["changes"]["full_name"] == {"from": "Before Name", "to": "After Name"}


def test_update_rejects_email_credentials_invalid_values_and_self_promotion(api_client) -> None:
    client, factory, _ = api_client
    admin = create_employee(factory, "update-admin@example.com", EmployeeRole.ADMIN)
    target = create_employee(factory, "update-target@example.com", EmployeeRole.RECRUITER)
    headers = bearer(client, admin.email)

    for payload in ({"email": "new@example.com"}, {"password": "New password 123!"}, {}, {"full_name": "  "}):
        assert client.patch(f"/api/v1/employees/{target.id}", json=payload, headers=headers).status_code == 422
    assert client.patch(
        f"/api/v1/employees/{target.id}", json={"role": "candidate"}, headers=headers
    ).status_code == 422
    assert client.patch(
        f"/api/v1/employees/{admin.id}", json={"role": "hr_manager"}, headers=headers
    ).status_code == 403
    with factory() as db:
        persisted = db.get(Employee, target.id)
        assert persisted is not None and persisted.email == "update-target@example.com"
        assert persisted.password_hash is not None


def test_deactivate_revokes_sessions_and_activation_restores_login(api_client) -> None:
    client, factory, _ = api_client
    admin = create_employee(factory, "status-admin@example.com", EmployeeRole.ADMIN)
    second_admin = create_employee(factory, "status-admin-2@example.com", EmployeeRole.ADMIN)
    target = create_employee(factory, "status-target@example.com", EmployeeRole.RECRUITER, name="Status Target")
    target_tokens = client.post(
        "/api/v1/auth/login", json={"email": target.email, "password": PASSWORD}
    ).json()
    headers = bearer(client, admin.email)

    deactivated = client.patch(
        f"/api/v1/employees/{target.id}", json={"status": "disabled"}, headers=headers
    )
    assert deactivated.status_code == 200 and deactivated.json()["status"] == "disabled"
    target_headers = {"Authorization": f"Bearer {target_tokens['access_token']}"}
    assert client.get("/api/v1/auth/me", headers=target_headers).status_code == 401
    assert client.post(
        "/api/v1/auth/refresh", json={"refresh_token": target_tokens["refresh_token"]}
    ).status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": target.email, "password": PASSWORD}).status_code == 401

    with factory() as db:
        sessions = db.scalars(select(RefreshSession).where(RefreshSession.employee_id == target.id)).all()
        assert sessions and all(session.revoked_at is not None for session in sessions)
        events = db.scalars(select(AuditLog).where(AuditLog.target_employee_id == target.id)).all()
        assert any(event.action == "employee.deactivated" for event in events)

    activated = client.patch(
        f"/api/v1/employees/{target.id}", json={"status": "active"}, headers=headers
    )
    assert activated.status_code == 200 and activated.json()["status"] == "active"
    assert client.post("/api/v1/auth/login", json={"email": target.email, "password": PASSWORD}).status_code == 200


def test_last_active_admin_cannot_be_demoted_or_deactivated(api_client) -> None:
    client, factory, _ = api_client
    last_admin = create_employee(factory, "last-admin@example.com", EmployeeRole.ADMIN)
    headers = bearer(client, last_admin.email)

    deactivate = client.patch(
        f"/api/v1/employees/{last_admin.id}", json={"status": "disabled"}, headers=headers
    )
    assert deactivate.status_code == 409
    with factory() as db:
        persisted = db.get(Employee, last_admin.id)
        assert persisted is not None
        assert persisted.role == EmployeeRole.ADMIN and persisted.status == EmployeeStatus.ACTIVE


def test_audit_records_are_append_only(api_client) -> None:
    client, factory, _ = api_client
    admin = create_employee(factory, "audit-admin@example.com", EmployeeRole.ADMIN)
    target = create_employee(factory, "audit-target@example.com", EmployeeRole.RECRUITER)
    headers = bearer(client, admin.email)
    response = client.patch(
        f"/api/v1/employees/{target.id}", json={"full_name": "Audit Target"}, headers=headers
    )
    assert response.status_code == 200
    with factory() as db:
        event = db.scalar(select(AuditLog).where(AuditLog.target_employee_id == target.id))
        assert event is not None
        event.details = {"corrupted": True}
        with pytest.raises(ValueError, match="append-only"):
            db.commit()

