from datetime import datetime, timezone
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class InterviewScheduleFields(BaseModel):
    starts_at: datetime
    ends_at: datetime
    timezone_name: str = Field(min_length=1, max_length=64)

    @field_validator("starts_at", "ends_at")
    @classmethod
    def require_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Interview timestamps must include a timezone offset")
        return value.astimezone(timezone.utc)

    @field_validator("timezone_name")
    @classmethod
    def require_iana_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone_name must be a valid IANA timezone") from exc
        return value

    @model_validator(mode="after")
    def validate_interval(self) -> "InterviewScheduleFields":
        if self.starts_at >= self.ends_at:
            raise ValueError("Interview end time must be later than start time")
        return self

    model_config = ConfigDict(extra="forbid")


class InterviewRoundCreate(InterviewScheduleFields):
    interviewer_ids: list[UUID] = Field(default_factory=list)

    @field_validator("interviewer_ids")
    @classmethod
    def require_unique_interviewers(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("Interviewer IDs must be unique")
        return value


class InterviewRoundPatch(BaseModel):
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    timezone_name: str | None = Field(default=None, min_length=1, max_length=64)
    status: Literal["completed", "cancelled"] | None = None

    @model_validator(mode="after")
    def validate_operation(self) -> "InterviewRoundPatch":
        schedule_values = (self.starts_at, self.ends_at, self.timezone_name)
        if self.status is not None:
            if any(value is not None for value in schedule_values):
                raise ValueError("A status transition cannot include schedule fields")
            return self
        if any(value is None for value in schedule_values):
            raise ValueError("Rescheduling requires start, end, and timezone_name")
        validated = InterviewScheduleFields(
            starts_at=self.starts_at,
            ends_at=self.ends_at,
            timezone_name=self.timezone_name,
        )
        self.starts_at = validated.starts_at
        self.ends_at = validated.ends_at
        self.timezone_name = validated.timezone_name
        return self

    model_config = ConfigDict(extra="forbid")


class InterviewerAssignmentResponse(BaseModel):
    id: UUID
    interviewer_employee_id: UUID
    interviewer_name: str | None
    assigned_at: datetime
    removed_by_id: UUID | None
    removed_at: datetime | None


class InterviewFeedbackRead(BaseModel):
    id: UUID
    interviewer_employee_id: UUID
    interviewer_name: str | None
    rating: int
    strengths: str
    areas_for_improvement: str
    recommendation: Literal["strong_hire", "hire", "no_hire", "strong_no_hire"]
    created_at: datetime
    updated_at: datetime


class InterviewFeedbackCreate(BaseModel):
    rating: int = Field(ge=1, le=5, strict=True)
    strengths: str
    areas_for_improvement: str
    recommendation: Literal["strong_hire", "hire", "no_hire", "strong_no_hire"]

    @field_validator("strengths", "areas_for_improvement")
    @classmethod
    def require_nonblank_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Feedback text must not be blank")
        if len(normalized) > 5000:
            raise ValueError("Feedback text must not exceed 5000 characters")
        return normalized

    model_config = ConfigDict(extra="forbid")


class InterviewFeedbackPatch(BaseModel):
    rating: int | None = Field(default=None, ge=1, le=5, strict=True)
    strengths: str | None = None
    areas_for_improvement: str | None = None
    recommendation: Literal["strong_hire", "hire", "no_hire", "strong_no_hire"] | None = None

    @field_validator("strengths", "areas_for_improvement")
    @classmethod
    def require_nonblank_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("Feedback text must not be blank")
        if len(normalized) > 5000:
            raise ValueError("Feedback text must not exceed 5000 characters")
        return normalized

    @model_validator(mode="after")
    def require_update_field(self) -> "InterviewFeedbackPatch":
        if not self.model_fields_set or all(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("At least one feedback field must be provided")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Feedback fields cannot be null")
        return self

    model_config = ConfigDict(extra="forbid")


class InterviewRoundResponse(BaseModel):
    id: UUID
    application_id: UUID | None = None
    candidate_full_name: str
    job_title: str
    starts_at: datetime
    ends_at: datetime
    timezone_name: str
    status: Literal["scheduled", "completed", "cancelled"]
    created_at: datetime
    updated_at: datetime
    interviewers: list[InterviewerAssignmentResponse]
    feedback: list[InterviewFeedbackRead]


class InterviewRoundPage(BaseModel):
    items: list[InterviewRoundResponse]
    page: int
    page_size: int
    total: int


