import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from jwt import InvalidTokenError
from pwdlib import PasswordHash

from app.core.config import Settings, get_settings

password_hasher = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password, password_hash)
    except Exception:
        return False


def hash_opaque_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def token_expiry(claims: dict[str, Any]) -> datetime:
    return datetime.fromtimestamp(int(claims["exp"]), timezone.utc)


def create_jwt(
    *,
    employee_id: uuid.UUID,
    session_id: uuid.UUID,
    token_type: str,
    expires_at: datetime,
    settings: Settings | None = None,
) -> str:
    config = settings or get_settings()
    now = utcnow()
    payload = {
        "sub": str(employee_id),
        "sid": str(session_id),
        "type": token_type,
        "iat": now,
        "exp": expires_at,
        "jti": secrets.token_urlsafe(16),
        "iss": "recruitflow-api",
    }
    return jwt.encode(payload, config.jwt_secret.get_secret_value(), algorithm=config.jwt_algorithm)


def decode_jwt(token: str, expected_type: str, settings: Settings | None = None) -> dict[str, Any]:
    config = settings or get_settings()
    try:
        payload = jwt.decode(
            token,
            config.jwt_secret.get_secret_value(),
            algorithms=[config.jwt_algorithm],
            issuer="recruitflow-api",
            options={"require": ["sub", "sid", "type", "iat", "exp", "jti", "iss"]},
        )
    except InvalidTokenError as exc:
        raise ValueError("Invalid or expired token") from exc
    if payload.get("type") != expected_type:
        raise ValueError("Incorrect token type")
    try:
        uuid.UUID(payload["sub"])
        uuid.UUID(payload["sid"])
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("Invalid token subject") from exc
    return payload


def access_expiry(settings: Settings | None = None) -> datetime:
    config = settings or get_settings()
    return utcnow() + timedelta(minutes=config.access_token_minutes)


def refresh_expiry(settings: Settings | None = None) -> datetime:
    config = settings or get_settings()
    return utcnow() + timedelta(days=config.refresh_token_days)
