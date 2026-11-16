"""Company-calendar helpers shared by job and application deadline rules."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.core.config import Settings, get_settings
from app.core.security import utcnow


def company_today(settings: Settings | None = None, *, now: datetime | None = None) -> date:
    config = settings or get_settings()
    return (now or utcnow()).astimezone(ZoneInfo(config.company_timezone)).date()
