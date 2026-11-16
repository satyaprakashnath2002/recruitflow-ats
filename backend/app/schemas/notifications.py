import enum
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class NotificationEventType(str, enum.Enum):
    INTERVIEW_SCHEDULED = "interview_scheduled"
    INTERVIEW_RESCHEDULED = "interview_rescheduled"
    INTERVIEW_CANCELLED = "interview_cancelled"
    INTERVIEW_COMPLETED = "interview_completed"
    INTERVIEW_REMINDER_24H = "interview_reminder_24h"
    INTERVIEW_REMINDER_1H = "interview_reminder_1h"
    OFFER_SENT = "offer_sent"
    OFFER_EXPIRY_3D = "offer_expiry_3d"
    OFFER_EXPIRY_1D = "offer_expiry_1d"


class NotificationResponse(BaseModel):
    id: UUID
    event_type: NotificationEventType
    title: str
    body: str
    resource_type: str
    resource_id: UUID | None
    offer_id: UUID | None = None
    created_at: datetime
    read_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class NotificationPage(BaseModel):
    items: list[NotificationResponse]
    page: int
    page_size: int
    total: int


class MarkAllReadResponse(BaseModel):
    updated: int
