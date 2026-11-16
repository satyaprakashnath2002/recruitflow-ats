import logging
import secrets
import uuid
from datetime import timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import bearer_scheme, get_current_employee, require_roles
from app.core.config import get_settings
from app.core.security import (
    access_expiry,
    as_utc,
    create_jwt,
    decode_jwt,
    hash_opaque_token,
    hash_password,
    new_opaque_token,
    refresh_expiry,
    utcnow,
    verify_password,
)
from app.db.session import get_db
from app.models.auth import (
    Employee,
    EmployeeInvitation,
    EmployeeRole,
    EmployeeStatus,
    PasswordResetToken,
    RefreshSession,
)
from app.schemas.auth import (
    EmployeeResponse,
    InvitationAcceptanceRequest,
    InvitationRequest,
    InvitationResponse,
    LoginRequest,
    PasswordResetConfirmation,
    PasswordResetRequest,
    TokenRefreshRequest,
    TokenResponse,
)
from app.services.auth_tokens import issue_token_pair
from app.services.email_delivery import EmailSender, SMTPEmailSender

logger = logging.getLogger(__name__)
router = APIRouter(tags=["authentication"])
ADMIN_ONLY = require_roles(EmployeeRole.ADMIN)
DUMMY_PASSWORD_HASH = hash_password("recruitflow-invalid-login-placeholder")


def get_email_sender() -> EmailSender:
    return SMTPEmailSender()


def auth_error(message: str = "Invalid or expired credential", code: int = 401) -> HTTPException:
    return HTTPException(
        status_code=code,
        detail={"status": "error", "message": message},
        headers={"WWW-Authenticate": "Bearer"} if code == 401 else None,
    )


@router.post("/auth/login", response_model=TokenResponse)
def login(data: LoginRequest, db: Session = Depends(get_db)) -> dict[str, object]:
    email = str(data.email).strip().lower()
    employee = db.scalar(select(Employee).where(Employee.email == email))
    stored_hash = employee.password_hash if employee and employee.password_hash else DUMMY_PASSWORD_HASH
    password_valid = verify_password(data.password, stored_hash)
    if (
        employee is None
        or employee.status != EmployeeStatus.ACTIVE
        or employee.password_hash is None
        or not password_valid
    ):
        raise auth_error("Invalid email or password")

    employee.last_login_at = utcnow()
    tokens = issue_token_pair(db, employee)
    db.commit()
    return tokens


@router.get("/auth/me", response_model=EmployeeResponse)
def current_profile(employee: Employee = Depends(get_current_employee)) -> Employee:
    return employee


@router.post("/auth/refresh", response_model=TokenResponse)
def refresh_access_token(data: TokenRefreshRequest, db: Session = Depends(get_db)) -> dict[str, object]:
    try:
        claims = decode_jwt(data.refresh_token, "refresh")
        employee_id, session_id = uuid.UUID(claims["sub"]), uuid.UUID(claims["sid"])
    except ValueError:
        raise auth_error() from None

    session = db.scalar(select(RefreshSession).where(RefreshSession.id == session_id).with_for_update())
    if session is None:
        raise auth_error()
    if session.revoked_at is not None:
        raise auth_error()
    if not secrets.compare_digest(session.token_hash, hash_opaque_token(data.refresh_token)):
        # A valid, previously rotated token was replayed. Revoke the associated session.
        session.revoked_at = utcnow()
        db.commit()
        raise auth_error()
    if as_utc(session.expires_at) <= utcnow():
        session.revoked_at = utcnow()
        db.commit()
        raise auth_error()

    employee = db.scalar(select(Employee).where(Employee.id == employee_id))
    if employee is None or employee.status != EmployeeStatus.ACTIVE or session.employee_id != employee_id:
        session.revoked_at = utcnow()
        db.commit()
        raise auth_error()

    config = get_settings()
    next_refresh_expiry = refresh_expiry(config)
    next_refresh = create_jwt(
        employee_id=employee.id,
        session_id=session.id,
        token_type="refresh",
        expires_at=next_refresh_expiry,
    )
    next_access_expiry = access_expiry(config)
    next_access = create_jwt(
        employee_id=employee.id,
        session_id=session.id,
        token_type="access",
        expires_at=next_access_expiry,
    )
    session.token_hash = hash_opaque_token(next_refresh)
    session.expires_at = next_refresh_expiry
    session.last_used_at = utcnow()
    db.commit()
    return {
        "access_token": next_access,
        "refresh_token": next_refresh,
        "token_type": "bearer",
        "expires_in": config.access_token_minutes * 60,
    }


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    employee: Employee = Depends(get_current_employee),
    credentials=Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Response:
    try:
        claims = decode_jwt(credentials.credentials, "access")
        session_id = uuid.UUID(claims["sid"])
    except (ValueError, AttributeError):
        raise auth_error() from None
    session = db.scalar(
        select(RefreshSession).where(
            RefreshSession.id == session_id,
            RefreshSession.employee_id == employee.id,
        )
    )
    if session is not None and session.revoked_at is None:
        session.revoked_at = utcnow()
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/employees/invitations", response_model=InvitationResponse, status_code=202)
def invite_employee(
    data: InvitationRequest,
    admin: Employee = Depends(ADMIN_ONLY),
    db: Session = Depends(get_db),
    email_sender: EmailSender = Depends(get_email_sender),
) -> InvitationResponse:
    email = str(data.email).strip().lower()
    existing = db.scalar(select(Employee).where(Employee.email == email))
    if existing is not None:
        raise auth_error("An employee account with this email already exists", code=409)

    now = utcnow()
    # A new invitation supersedes prior unaccepted invitations for the same address.
    previous = db.scalars(
        select(EmployeeInvitation).where(
            EmployeeInvitation.email == email,
            EmployeeInvitation.accepted_at.is_(None),
            EmployeeInvitation.revoked_at.is_(None),
        )
    ).all()
    for invitation in previous:
        invitation.revoked_at = now

    config = get_settings()
    token = new_opaque_token()
    expiry = now + timedelta(hours=config.invitation_token_hours)
    invitation = EmployeeInvitation(
        email=email,
        role=data.role,
        token_hash=hash_opaque_token(token),
        expires_at=expiry,
        invited_by_id=admin.id,
    )
    db.add(invitation)
    db.commit()
    db.refresh(invitation)

    invitation_link = f"{config.frontend_base_url.rstrip('/')}/accept-invitation?token={quote(token)}"
    try:
        email_sender.send(
            email,
            "Your RecruitFlow employee invitation",
            f"You have been invited to RecruitFlow. Accept the invitation before {expiry.isoformat()}:\n{invitation_link}\n",
        )
    except Exception:
        invitation.revoked_at = utcnow()
        db.commit()
        logger.warning("Employee invitation delivery failed; invitation revoked")
        raise HTTPException(
            status_code=503,
            detail={"status": "error", "message": "Invitation delivery is unavailable"},
        ) from None

    return InvitationResponse(id=invitation.id, email=email, role=invitation.role, expires_at=expiry)


@router.post("/employees/invitations/accept", response_model=EmployeeResponse, status_code=201)
def accept_invitation(data: InvitationAcceptanceRequest, db: Session = Depends(get_db)) -> Employee:
    now = utcnow()
    invitation = db.scalar(
        select(EmployeeInvitation)
        .where(EmployeeInvitation.token_hash == hash_opaque_token(data.token))
        .with_for_update()
    )
    if (
        invitation is None
        or invitation.accepted_at is not None
        or invitation.revoked_at is not None
        or as_utc(invitation.expires_at) <= now
    ):
        raise auth_error("Invitation is invalid or expired", code=400)

    employee = Employee(
        full_name=data.full_name,
        email=invitation.email,
        role=invitation.role,
        status=EmployeeStatus.ACTIVE,
        password_hash=hash_password(data.password),
    )
    invitation.accepted_at = now
    db.add(employee)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise auth_error("Invitation is invalid or expired", code=400) from None
    db.refresh(employee)
    return employee


@router.post("/auth/password-reset/request", status_code=202)
def request_password_reset(
    data: PasswordResetRequest,
    db: Session = Depends(get_db),
    email_sender: EmailSender = Depends(get_email_sender),
) -> dict[str, str]:
    generic_response = {"message": "If the account exists, password reset instructions will be sent."}
    email = str(data.email).strip().lower()
    employee = db.scalar(
        select(Employee).where(Employee.email == email, Employee.status == EmployeeStatus.ACTIVE)
    )
    if employee is None:
        return generic_response

    now = utcnow()
    outstanding = db.scalars(
        select(PasswordResetToken).where(
            PasswordResetToken.employee_id == employee.id,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.revoked_at.is_(None),
        )
    ).all()
    for prior in outstanding:
        prior.revoked_at = now

    token = new_opaque_token()
    config = get_settings()
    expiry = now + timedelta(minutes=config.password_reset_token_minutes)
    reset = PasswordResetToken(
        employee_id=employee.id,
        token_hash=hash_opaque_token(token),
        expires_at=expiry,
    )
    db.add(reset)
    db.commit()

    reset_link = f"{config.frontend_base_url.rstrip('/')}/reset-password?token={quote(token)}"
    try:
        email_sender.send(
            email,
            "Reset your RecruitFlow password",
            f"Use this link before {expiry.isoformat()} to reset your password:\n{reset_link}\n",
        )
    except Exception:
        reset.revoked_at = utcnow()
        db.commit()
        logger.warning("Password reset delivery failed")
    return generic_response


@router.post("/auth/password-reset/confirm")
def confirm_password_reset(data: PasswordResetConfirmation, db: Session = Depends(get_db)) -> dict[str, str]:
    now = utcnow()
    reset = db.scalar(
        select(PasswordResetToken)
        .where(PasswordResetToken.token_hash == hash_opaque_token(data.token))
        .with_for_update()
    )
    if (
        reset is None
        or reset.used_at is not None
        or reset.revoked_at is not None
        or as_utc(reset.expires_at) <= now
    ):
        raise auth_error("Password reset token is invalid or expired", code=400)

    employee = db.scalar(select(Employee).where(Employee.id == reset.employee_id))
    if employee is None or employee.status != EmployeeStatus.ACTIVE:
        raise auth_error("Password reset token is invalid or expired", code=400)

    employee.password_hash = hash_password(data.new_password)
    reset.used_at = now
    other_resets = db.scalars(
        select(PasswordResetToken).where(
            PasswordResetToken.employee_id == employee.id,
            PasswordResetToken.id != reset.id,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.revoked_at.is_(None),
        )
    ).all()
    for other in other_resets:
        other.revoked_at = now
    sessions = db.scalars(
        select(RefreshSession).where(
            RefreshSession.employee_id == employee.id,
            RefreshSession.revoked_at.is_(None),
        )
    ).all()
    for session in sessions:
        session.revoked_at = now
    db.commit()
    return {"message": "Password has been reset. Please sign in again."}
