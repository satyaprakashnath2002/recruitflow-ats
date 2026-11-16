import enum
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class ApplicationSource(str, enum.Enum):
    PUBLIC_PORTAL = "public_portal"
    RECRUITER = "recruiter"
    REFERRAL = "referral"


class ApplicationStage(str, enum.Enum):
    APPLIED = "applied"
    SCREENING = "screening"
    SHORTLISTED = "shortlisted"
    INTERVIEW = "interview"
    SELECTED = "selected"
    OFFER = "offer"
    HIRED = "hired"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class CandidateInput(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=40)
    location: str | None = Field(default=None, max_length=200)
    linkedin_url: str | None = Field(default=None, max_length=2048)

    @field_validator("full_name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("full_name cannot be blank")
        return value

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("phone", "location", "linkedin_url")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class CandidateUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=40)
    location: str | None = Field(default=None, max_length=200)
    linkedin_url: str | None = Field(default=None, max_length=2048)

    model_config = ConfigDict(extra="forbid")

    @field_validator("full_name")
    @classmethod
    def trim_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("full_name cannot be blank")
        return cleaned

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("phone", "location", "linkedin_url")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class CandidateResponse(BaseModel):
    id: UUID
    full_name: str
    email: EmailStr
    phone: str | None
    location: str | None
    linkedin_url: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CandidatePage(BaseModel):
    items: list[CandidateResponse]
    page: int
    page_size: int
    total: int


class ApplicationCreate(BaseModel):
    job_id: UUID
    candidate: CandidateInput
    source: ApplicationSource

    model_config = ConfigDict(extra="forbid")


class ApplicationPatch(BaseModel):
    """No Application business field is currently safe to update through PATCH.

    Lifecycle, identity, source and ownership data have dedicated rules or
    endpoints. New mutable fields require an explicit contract update.
    """

    model_config = ConfigDict(extra="forbid")


class ApplicationStageChange(BaseModel):
    stage: ApplicationStage

    model_config = ConfigDict(extra="forbid")


class ApplicationResponse(BaseModel):
    id: UUID
    candidate_id: UUID
    job_id: UUID
    source: ApplicationSource
    stage: ApplicationStage
    created_by_id: UUID | None
    created_at: datetime
    updated_at: datetime
    candidate: CandidateResponse

    model_config = ConfigDict(from_attributes=True)


class ApplicationPage(BaseModel):
    items: list[ApplicationResponse]
    page: int
    page_size: int
    total: int


class ApplicationNoteCreate(BaseModel):
    body: str = Field(min_length=1)

    @field_validator("body")
    @classmethod
    def body_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Note body cannot be blank")
        return value


class ApplicationNoteResponse(BaseModel):
    id: UUID
    application_id: UUID
    author_employee_id: UUID
    author_name: str | None
    body: str
    created_at: datetime


class ApplicationNotePage(BaseModel):
    items: list[ApplicationNoteResponse]


class DocumentResponse(BaseModel):
    id: UUID
    application_id: UUID
    media_type: str
    byte_size: int
    checksum_sha256: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
