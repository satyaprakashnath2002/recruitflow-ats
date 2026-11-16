import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_employee
from app.core.security import utcnow
from app.db.session import get_db
from app.models.auth import Employee
from app.models.notifications import Notification
from app.schemas.notifications import (
    MarkAllReadResponse,
    NotificationEventType,
    NotificationPage,
    NotificationResponse,
)

router = APIRouter(tags=["notifications"])


@router.get("/notifications", response_model=NotificationPage, summary="List the current employee's notifications")
def list_notifications(
    unread: bool | None = None,
    event_type: NotificationEventType | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    actor: Employee = Depends(get_current_employee),
    db: Session = Depends(get_db),
) -> NotificationPage:
    criteria = [Notification.recipient_employee_id == actor.id]
    if unread is True:
        criteria.append(Notification.read_at.is_(None))
    elif unread is False:
        criteria.append(Notification.read_at.is_not(None))
    if event_type is not None:
        criteria.append(Notification.event_type == event_type.value)
    total = db.scalar(select(func.count(Notification.id)).where(*criteria)) or 0
    items = db.scalars(
        select(Notification).where(*criteria)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    return NotificationPage(items=items, page=page, page_size=page_size, total=total)


@router.patch("/notifications/{notification_id}", response_model=NotificationResponse, summary="Mark a notification as read")
def mark_notification_read(
    notification_id: uuid.UUID,
    actor: Employee = Depends(get_current_employee),
    db: Session = Depends(get_db),
) -> Notification:
    notification = db.scalar(select(Notification).where(
        Notification.id == notification_id,
        Notification.recipient_employee_id == actor.id,
    ).with_for_update())
    if notification is None:
        raise HTTPException(status_code=404, detail={"status": "not_found", "message": "Notification not found"})
    if notification.read_at is None:
        notification.read_at = utcnow()
        db.commit()
        db.refresh(notification)
    return notification


@router.post("/notifications/mark-all-read", response_model=MarkAllReadResponse, summary="Mark all current employee notifications as read")
def mark_all_notifications_read(
    actor: Employee = Depends(get_current_employee),
    db: Session = Depends(get_db),
) -> MarkAllReadResponse:
    now = datetime.now(timezone.utc)
    result = db.execute(update(Notification).where(
        Notification.recipient_employee_id == actor.id,
        Notification.read_at.is_(None),
    ).values(read_at=now))
    db.commit()
    return MarkAllReadResponse(updated=result.rowcount or 0)
