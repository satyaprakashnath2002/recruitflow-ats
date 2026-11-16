import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health", tags=["health"])
def liveness() -> dict[str, str]:
    return {"status": "ok", "service": "recruitflow-api"}


@router.get(
    "/health/ready",
    tags=["health"],
    responses={503: {"description": "Database unavailable"}},
)
def readiness(db: Session = Depends(get_db)) -> dict[str, str]:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.warning("Readiness check failed: database unavailable")
        raise HTTPException(
            status_code=503,
            detail={"status": "not_ready", "database": "unavailable"},
        ) from exc
    return {"status": "ok", "database": "available"}
