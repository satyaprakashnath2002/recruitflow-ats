import enum
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.core.config import get_settings
from app.models.auth import EmployeeRole, EmployeeStatus


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class TokenRefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class EmployeeResponse(BaseModel):
    id: UUID
    full_name: str | None
    email: EmailStr
    role: EmployeeRole
    status: EmployeeStatus

    model_config = ConfigDict(from_attributes=True)


class InvitationRequest(BaseModel):
    email: EmailStr
    role: EmployeeRole


class InvitationResponse(BaseModel):
    id: UUID
    email: EmailStr
    role: EmployeeRole
    expires_at: datetime
    message: str = "Invitation sent"


class InvitationAcceptanceRequest(BaseModel):
    token: str = Field(min_length=20, max_length=256)
    full_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("password")
    @classmethod
    def enforce_password_minimum(cls, value: str) -> str:
        minimum = get_settings().password_min_length
        if len(value) < minimum:
            raise ValueError(f"Password must be at least {minimum} characters")
        return value

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Full name cannot be blank")
        return normalized


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirmation(BaseModel):
    token: str = Field(min_length=20, max_length=256)
    new_password: str = Field(min_length=1, max_length=128)

    @field_validator("new_password")
    @classmethod
    def enforce_password_minimum(cls, value: str) -> str:
        minimum = get_settings().password_min_length
        if len(value) < minimum:
            raise ValueError(f"Password must be at least {minimum} characters")
        return value


class ManagedEmployeeStatus(str, enum.Enum):
    ACTIVE = "active"
    DISABLED = "disabled"


class EmployeeUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    role: EmployeeRole | None = None
    status: ManagedEmployeeStatus | None = None

    @model_validator(mode="after")
    def validate_patch(self) -> "EmployeeUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one employee field must be provided")
        for field in self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("Full name cannot be blank")
        return normalized


class EmployeeDirectoryItem(BaseModel):
    id: UUID
    full_name: str | None
    email: EmailStr
    role: EmployeeRole
    status: EmployeeStatus
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class EmployeeDirectoryPage(BaseModel):
    items: list[EmployeeDirectoryItem]
    page: int
    page_size: int
    total: int
