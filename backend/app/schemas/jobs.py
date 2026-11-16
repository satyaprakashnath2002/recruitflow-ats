from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.time import company_today
from app.models.jobs import JobStatus


def _clean_text(value: str, field_name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} cannot be blank")
    return cleaned


def validate_ranges(
    min_experience: Decimal | None,
    max_experience: Decimal | None,
    salary_min: Decimal | None,
    salary_max: Decimal | None,
) -> None:
    if min_experience is not None and min_experience < 0:
        raise ValueError("Minimum experience cannot be negative")
    if max_experience is not None and max_experience < 0:
        raise ValueError("Maximum experience cannot be negative")
    if min_experience is not None and max_experience is not None and min_experience > max_experience:
        raise ValueError("Minimum experience cannot exceed maximum experience")
    if salary_min is not None and salary_min < 0:
        raise ValueError("Minimum salary cannot be negative")
    if salary_max is not None and salary_max < 0:
        raise ValueError("Maximum salary cannot be negative")
    if salary_min is not None and salary_max is not None and salary_min > salary_max:
        raise ValueError("Minimum salary cannot exceed maximum salary")


class JobFields(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    department: str = Field(min_length=1, max_length=120)
    employment_type: str = Field(min_length=1, max_length=50)
    location: str = Field(min_length=1, max_length=200)
    work_mode: str = Field(min_length=1, max_length=50)
    min_experience: Decimal | None = Field(default=None, ge=0, max_digits=5, decimal_places=2)
    max_experience: Decimal | None = Field(default=None, ge=0, max_digits=5, decimal_places=2)
    salary_min: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    salary_max: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    description: str = Field(min_length=1, max_length=20000)
    required_skills: list[str] = Field(default_factory=list)
    qualifications: list[str] = Field(default_factory=list)
    vacancies: int = Field(gt=0)
    application_deadline: date | None = None

    @field_validator("title", "department", "employment_type", "location", "work_mode", "description")
    @classmethod
    def trim_required_text(cls, value: str, info) -> str:
        return _clean_text(value, info.field_name.replace("_", " ").title())

    @field_validator("required_skills", "qualifications")
    @classmethod
    def clean_text_lists(cls, values: list[str]) -> list[str]:
        return [_clean_text(value, "List item") for value in values]

    @field_validator("application_deadline")
    @classmethod
    def deadline_must_be_future(cls, value: date | None) -> date | None:
        if value is not None and value <= company_today():
            raise ValueError("Application deadline must be a future date")
        return value

    @model_validator(mode="after")
    def validate_numeric_ranges(self) -> "JobFields":
        validate_ranges(self.min_experience, self.max_experience, self.salary_min, self.salary_max)
        return self


class JobCreate(JobFields):
    pass


class JobUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    department: str | None = Field(default=None, min_length=1, max_length=120)
    employment_type: str | None = Field(default=None, min_length=1, max_length=50)
    location: str | None = Field(default=None, min_length=1, max_length=200)
    work_mode: str | None = Field(default=None, min_length=1, max_length=50)
    min_experience: Decimal | None = Field(default=None, ge=0, max_digits=5, decimal_places=2)
    max_experience: Decimal | None = Field(default=None, ge=0, max_digits=5, decimal_places=2)
    salary_min: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    salary_max: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    description: str | None = Field(default=None, min_length=1, max_length=20000)
    required_skills: list[str] | None = None
    qualifications: list[str] | None = None
    vacancies: int | None = Field(default=None, gt=0)
    application_deadline: date | None = None

    @field_validator("title", "department", "employment_type", "location", "work_mode", "description")
    @classmethod
    def trim_optional_text(cls, value: str | None, info) -> str | None:
        return None if value is None else _clean_text(value, info.field_name.replace("_", " ").title())

    @field_validator("required_skills", "qualifications")
    @classmethod
    def clean_optional_lists(cls, values: list[str] | None) -> list[str] | None:
        return None if values is None else [_clean_text(value, "List item") for value in values]

    @field_validator("application_deadline")
    @classmethod
    def deadline_must_be_future(cls, value: date | None) -> date | None:
        if value is not None and value <= company_today():
            raise ValueError("Application deadline must be a future date")
        return value

    @model_validator(mode="after")
    def validate_patch(self) -> "JobUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one job field must be provided")
        for field in self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        validate_ranges(self.min_experience, self.max_experience, self.salary_min, self.salary_max)
        return self


class JobResponse(BaseModel):
    id: UUID
    title: str
    department: str
    employment_type: str
    location: str
    work_mode: str
    min_experience: Decimal | None
    max_experience: Decimal | None
    salary_min: Decimal | None
    salary_max: Decimal | None
    description: str
    required_skills: list[str]
    qualifications: list[str]
    vacancies: int
    application_deadline: date | None
    status: JobStatus
    created_by_id: UUID
    updated_by_id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class JobPublicResponse(BaseModel):
    id: UUID
    title: str
    department: str
    employment_type: str
    location: str
    work_mode: str
    min_experience: Decimal | None
    max_experience: Decimal | None
    description: str
    required_skills: list[str]
    qualifications: list[str]
    vacancies: int
    application_deadline: date | None
    status: JobStatus

    model_config = ConfigDict(from_attributes=True)


class JobPage(BaseModel):
    items: list[JobResponse]
    page: int
    page_size: int
    total: int


class PublicJobPage(BaseModel):
    items: list[JobPublicResponse]
    page: int
    page_size: int
    total: int


class JobAssignmentResponse(BaseModel):
    employee_id: UUID
    full_name: str | None
    email: str
    assigned_by_id: UUID
    assigned_at: datetime


class JobAssignmentList(BaseModel):
    job_id: UUID
    recruiters: list[JobAssignmentResponse]
