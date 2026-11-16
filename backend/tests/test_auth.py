import re
import uuid
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.api.dependencies import require_roles
from app.core.config import get_settings
from app.core.security import create_jwt, hash_password, hash_opaque_token, utcnow, verify_password
from app.models.auth import (
    Employee,
    EmployeeInvitation,
    EmployeeRole,
    EmployeeStatus,
    PasswordResetToken,
    RefreshSession,
)

PASSWORD = "Correct horse battery 77!"


def create_employee(factory: sessionmaker, email: str, role: EmployeeRole, password: str = PASSWORD) -> Employee:
    with factory() as db:
        employee = Employee(
            email=email,
            role=role,
            status=EmployeeStatus.ACTIVE,
            password_hash=hash_password(password),
        )
        db.add(employee)
        db.commit()
        db.refresh(employee)
        return employee


def login(client, email: str, password: str = PASSWORD) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return response.json()


def token_from_message(body: str) -> str:
    match = re.search(r"token=([A-Za-z0-9_-]+)", body)
    assert match is not None
    return match.group(1)


def test_login_profile_and_password_hash(api_client) -> None:
    client, factory, _ = api_client
    employee = create_employee(factory, "admin@example.com", EmployeeRole.ADMIN)

    response = client.post("/api/v1/auth/login", json={"email": employee.email, "password": PASSWORD})
    assert response.status_code == 200
    tokens = response.json()
    assert tokens["token_type"] == "bearer"
    assert tokens["access_token"] != PASSWORD
    assert "password_hash" not in tokens

    profile = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert profile.status_code == 200
    assert profile.json() == {
        "id": str(employee.id),
        "full_name": None,
        "email": employee.email,
        "role": "admin",
        "status": "active",
    }
    with factory() as db:
        stored = db.get(Employee, employee.id)
        assert stored is not None and stored.password_hash != PASSWORD
        assert verify_password(PASSWORD, stored.password_hash)


@pytest.mark.parametrize(
    ("email", "password", "status"),
    [
        ("missing@example.com", PASSWORD, 401),
        ("recruiter@example.com", "incorrect password", 401),
    ],
)
def test_login_rejects_unknown_or_invalid_credentials(api_client, email, password, status) -> None:
    client, factory, _ = api_client
    create_employee(factory, "recruiter@example.com", EmployeeRole.RECRUITER)
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == status
    assert response.json()["error"]["message"] == "Invalid email or password"


def test_inactive_employee_cannot_login(api_client) -> None:
    client, factory, _ = api_client
    with factory() as db:
        db.add(
            Employee(
                email="inactive@example.com",
                role=EmployeeRole.INTERVIEWER,
                status=EmployeeStatus.DISABLED,
                password_hash=hash_password(PASSWORD),
            )
        )
        db.commit()
    response = client.post("/api/v1/auth/login", json={"email": "inactive@example.com", "password": PASSWORD})
    assert response.status_code == 401


def test_expired_invalid_and_wrong_purpose_access_tokens_are_rejected(api_client) -> None:
    client, factory, _ = api_client
    employee = create_employee(factory, "hr@example.com", EmployeeRole.HR_MANAGER)
    with factory() as db:
        session = RefreshSession(
            employee_id=employee.id,
            token_hash="f" * 64,
            expires_at=utcnow() + timedelta(days=1),
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        session_id = session.id

    expired_access = create_jwt(
        employee_id=employee.id,
        session_id=session_id,
        token_type="access",
        expires_at=utcnow() - timedelta(minutes=1),
    )
    wrong_type = create_jwt(
        employee_id=employee.id,
        session_id=session_id,
        token_type="refresh",
        expires_at=utcnow() + timedelta(minutes=5),
    )
    for token in (expired_access, "not-a-jwt", wrong_type):
        response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401
    assert client.get("/api/v1/auth/me").status_code == 401


def test_refresh_rotates_and_reuse_revokes_session(api_client) -> None:
    client, factory, _ = api_client
    employee = create_employee(factory, "refresh@example.com", EmployeeRole.RECRUITER)
    original = login(client, employee.email)
    rotated_response = client.post("/api/v1/auth/refresh", json={"refresh_token": original["refresh_token"]})
    assert rotated_response.status_code == 200
    rotated = rotated_response.json()
    assert rotated["refresh_token"] != original["refresh_token"]
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": original["refresh_token"]}).status_code == 401
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {rotated['access_token']}"}).status_code == 401

    with factory() as db:
        sessions = db.scalars(select(RefreshSession).where(RefreshSession.employee_id == employee.id)).all()
        assert len(sessions) == 1 and sessions[0].revoked_at is not None


def test_logout_revokes_access_and_refresh_tokens(api_client) -> None:
    client, _, _ = api_client
    client, factory, _ = api_client
    tokens = login(client, create_employee(factory, "logout@example.com", EmployeeRole.ADMIN).email)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401


@pytest.mark.parametrize("role", list(EmployeeRole))
def test_only_admin_can_invite(api_client, role: EmployeeRole) -> None:
    client, factory, _ = api_client
    employee = create_employee(factory, f"{role.value}@example.com", role)
    tokens = login(client, employee.email)
    response = client.post(
        "/api/v1/employees/invitations",
        json={"email": f"invite-{role.value}@example.com", "role": "interviewer"},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == (202 if role == EmployeeRole.ADMIN else 403)


@pytest.mark.parametrize("role", list(EmployeeRole))
def test_reusable_role_dependency_accepts_each_employee_role(role: EmployeeRole) -> None:
    employee = Employee(
        email=f"{role.value}@example.com",
        role=role,
        status=EmployeeStatus.ACTIVE,
        password_hash="test-only",
    )
    assert require_roles(role)(employee) is employee


def test_invitation_acceptance_is_one_time_and_stores_hash(api_client, caplog) -> None:
    client, factory, mailer = api_client
    admin = create_employee(factory, "inviter@example.com", EmployeeRole.ADMIN)
    tokens = login(client, admin.email)
    response = client.post(
        "/api/v1/employees/invitations",
        json={"email": "new-hire@example.com", "role": "hr_manager"},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 202
    assert "token" not in response.text
    raw_token = token_from_message(mailer.messages[-1][2])
    assert raw_token not in caplog.text

    accepted = client.post(
        "/api/v1/employees/invitations/accept",
        json={"token": raw_token, "full_name": "New Hire", "password": PASSWORD},
    )
    assert accepted.status_code == 201
    assert accepted.json()["role"] == "hr_manager"
    assert accepted.json()["full_name"] == "New Hire"
    assert "password_hash" not in accepted.text and raw_token not in accepted.text
    assert client.post(
        "/api/v1/employees/invitations/accept",
        json={"token": raw_token, "full_name": "New Hire", "password": PASSWORD},
    ).status_code == 400

    with factory() as db:
        record = db.get(EmployeeInvitation, uuid.UUID(response.json()["id"]))
        assert record is not None and record.token_hash == hash_opaque_token(raw_token)
        new_employee = db.scalar(select(Employee).where(Employee.email == "new-hire@example.com"))
        assert new_employee is not None and new_employee.password_hash != PASSWORD


def test_invalid_and_expired_invitation_rejected(api_client) -> None:
    client, factory, mailer = api_client
    admin = create_employee(factory, "inviter2@example.com", EmployeeRole.ADMIN)
    tokens = login(client, admin.email)
    response = client.post(
        "/api/v1/employees/invitations",
        json={"email": "expired@example.com", "role": "recruiter"},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    raw_token = token_from_message(mailer.messages[-1][2])
    with factory() as db:
        invitation = db.get(EmployeeInvitation, uuid.UUID(response.json()["id"]))
        assert invitation is not None
        invitation.expires_at = utcnow() - timedelta(minutes=1)
        db.commit()

    assert client.post(
        "/api/v1/employees/invitations/accept",
        json={"token": "bad-token-0123456789", "full_name": "Expired Hire", "password": PASSWORD},
    ).status_code == 400
    assert client.post(
        "/api/v1/employees/invitations/accept",
        json={"token": raw_token, "full_name": "Expired Hire", "password": PASSWORD},
    ).status_code == 400


def test_password_reset_is_generic_and_revokes_sessions(api_client) -> None:
    client, factory, mailer = api_client
    employee = create_employee(factory, "reset@example.com", EmployeeRole.INTERVIEWER)
    old_tokens = login(client, employee.email)
    existing = client.post("/api/v1/auth/password-reset/request", json={"email": employee.email})
    missing = client.post("/api/v1/auth/password-reset/request", json={"email": "unknown@example.com"})
    assert existing.status_code == missing.status_code == 202
    assert existing.json() == missing.json()
    assert "token" not in existing.text
    raw_token = token_from_message(mailer.messages[-1][2])
    assert raw_token not in existing.text

    changed = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": raw_token, "new_password": "New secure password 889!"},
    )
    assert changed.status_code == 200
    assert client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {old_tokens['access_token']}"}
    ).status_code == 401
    assert client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": raw_token, "new_password": "Another secure password 223!"},
    ).status_code == 400
    assert client.post(
        "/api/v1/auth/login", json={"email": employee.email, "password": "New secure password 889!"}
    ).status_code == 200


def test_expired_reset_token_rejected(api_client) -> None:
    client, factory, mailer = api_client
    employee = create_employee(factory, "reset-expired@example.com", EmployeeRole.RECRUITER)
    client.post("/api/v1/auth/password-reset/request", json={"email": employee.email})
    raw_token = token_from_message(mailer.messages[-1][2])
    with factory() as db:
        reset = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_opaque_token(raw_token)))
        assert reset is not None
        reset.expires_at = utcnow() - timedelta(minutes=1)
        db.commit()
    response = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": raw_token, "new_password": "New secure password 889!"},
    )
    assert response.status_code == 400
    invalid = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": "invalid-reset-token-123456", "new_password": "New secure password 889!"},
    )
    assert invalid.status_code == 400


def test_rbac_dependency_denies_interviewer_final_decision() -> None:
    interviewer = Employee(
        email="interviewer@example.com",
        role=EmployeeRole.INTERVIEWER,
        status=EmployeeStatus.ACTIVE,
        password_hash="not-used",
    )
    decision_policy = require_roles(EmployeeRole.HR_MANAGER)
    with pytest.raises(HTTPException) as error:
        decision_policy(interviewer)
    assert error.value.status_code == 403


def test_employee_email_unique_constraint(api_client) -> None:
    _, factory, _ = api_client
    create_employee(factory, "unique@example.com", EmployeeRole.RECRUITER)
    with factory() as db:
        db.add(Employee(email="unique@example.com", role=EmployeeRole.INTERVIEWER,
                        status=EmployeeStatus.ACTIVE, password_hash=hash_password(PASSWORD)))
        with pytest.raises(IntegrityError):
            db.commit()

