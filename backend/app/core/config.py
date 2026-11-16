from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Backend configuration loaded from environment or a local .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["development", "staging", "production", "test"] = "development"
    app_name: str = "RecruitFlow API"
    log_level: str = "INFO"
    cors_origins: str = ""
    database_url: SecretStr
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_max_overflow: int = Field(default=10, ge=0, le=100)
    db_pool_timeout: int = Field(default=30, ge=1, le=300)
    db_pool_recycle: int = Field(default=1800, ge=60)
    jwt_secret: SecretStr
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    access_token_minutes: int = Field(default=15, ge=1, le=60)
    refresh_token_days: int = Field(default=7, ge=1, le=90)
    invitation_token_hours: int = Field(default=48, ge=1, le=168)
    password_reset_token_minutes: int = Field(default=60, ge=5, le=1440)
    password_min_length: int = Field(default=12, ge=10, le=128)
    frontend_base_url: str = "http://localhost:3000"
    company_timezone: str = "Asia/Kolkata"
    notification_poll_seconds: int = Field(default=30, ge=5, le=3600)
    document_storage_path: Path = Path("var/private-documents")
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from_email: str | None = None
    smtp_starttls: bool = True

    @field_validator("database_url")
    @classmethod
    def database_url_must_not_be_blank(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("DATABASE_URL must not be blank")
        return value

    @field_validator("jwt_secret")
    @classmethod
    def jwt_secret_must_be_strong(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("JWT_SECRET must contain at least 32 characters")
        return value

    @model_validator(mode="after")
    def validate_smtp_security(self) -> "Settings":
        if self.smtp_host and not self.smtp_from_email:
            raise ValueError("SMTP_FROM_EMAIL is required when SMTP_HOST is configured")
        if self.smtp_host and not self.smtp_starttls:
            raise ValueError("SMTP_STARTTLS must remain enabled for this SMTP configuration")
        if bool(self.smtp_username) != bool(self.smtp_password):
            raise ValueError("SMTP_USERNAME and SMTP_PASSWORD must be configured together")
        return self

    @field_validator("company_timezone")
    @classmethod
    def company_timezone_must_be_valid(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("COMPANY_TIMEZONE must be a valid IANA timezone") from exc
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
