import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.security import (
    access_expiry,
    create_jwt,
    hash_opaque_token,
    refresh_expiry,
)
from app.models.auth import Employee, RefreshSession


def issue_token_pair(db: Session, employee: Employee, settings: Settings | None = None) -> dict[str, object]:
    config = settings or get_settings()
    session_id = uuid.uuid4()
    refresh_until = refresh_expiry(config)
    refresh_token = create_jwt(
        employee_id=employee.id,
        session_id=session_id,
        token_type="refresh",
        expires_at=refresh_until,
        settings=config,
    )
    access_until = access_expiry(config)
    access_token = create_jwt(
        employee_id=employee.id,
        session_id=session_id,
        token_type="access",
        expires_at=access_until,
        settings=config,
    )
    db.add(
        RefreshSession(
            id=session_id,
            employee_id=employee.id,
            token_hash=hash_opaque_token(refresh_token),
            expires_at=refresh_until,
        )
    )
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": config.access_token_minutes * 60,
    }


def session_is_active(session: RefreshSession, now: datetime | None = None) -> bool:
    moment = now or datetime.now(timezone.utc)
    expiry = session.expires_at
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return session.revoked_at is None and expiry > moment


def get_session(db: Session, session_id: uuid.UUID) -> RefreshSession | None:
    return db.scalar(select(RefreshSession).where(RefreshSession.id == session_id))
