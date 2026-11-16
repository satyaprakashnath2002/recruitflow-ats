from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.security import as_utc
from app.models.offers import OfferStatus


class OfferTermsInput(BaseModel):
    compensation_amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    compensation_currency: str = Field(pattern=r"^[A-Z]{3}$")
    compensation_basis: str = Field(min_length=1, max_length=80)
    expires_at: datetime

    @field_validator("compensation_basis")
    @classmethod
    def trim_compensation_basis(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Compensation basis must not be blank")
        return value

    @field_validator("expires_at")
    @classmethod
    def require_aware_expiry(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expires_at must include a UTC offset")
        return as_utc(value)


class OfferRevisionResponse(BaseModel):
    id: UUID
    revision_number: int
    compensation_amount: Decimal
    compensation_currency: str
    compensation_basis: str
    expires_at: datetime
    letter_size: int
    created_by_employee_id: UUID
    created_at: datetime


class OfferResponse(BaseModel):
    id: UUID
    application_id: UUID
    status: OfferStatus
    current_revision_number: int
    approved_revision_number: int | None
    approved_by_employee_id: UUID | None
    approved_at: datetime | None
    sent_at: datetime | None
    responded_at: datetime | None
    withdrawn_at: datetime | None
    expired_at: datetime | None
    created_by_employee_id: UUID
    created_at: datetime
    updated_at: datetime
    current_revision: OfferRevisionResponse


class OfferPage(BaseModel):
    items: list[OfferResponse]
    page: int
    page_size: int
    total: int


class OfferRevisionPage(BaseModel):
    items: list[OfferRevisionResponse]


class OfferPublicResponse(BaseModel):
    job_title: str
    compensation_amount: Decimal
    compensation_currency: str
    compensation_basis: str
    expires_at: datetime
    response: Literal["accepted", "rejected"] | None


class OfferCandidateResponse(BaseModel):
    response: Literal["accepted", "rejected"]

    model_config = ConfigDict(extra="forbid")


class OfferResponseReceipt(BaseModel):
    status: Literal["accepted", "rejected"]
    responded_at: datetime
