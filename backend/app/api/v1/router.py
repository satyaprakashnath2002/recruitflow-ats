from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.applications import router as applications_router
from app.api.v1.employees import router as employees_router
from app.api.v1.health import router as health_router
from app.api.v1.jobs import router as jobs_router
from app.api.v1.interviews import router as interviews_router
from app.api.v1.notifications import router as notifications_router
from app.api.v1.offers import router as offers_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(employees_router)
api_router.include_router(jobs_router)
api_router.include_router(applications_router)
api_router.include_router(interviews_router)
api_router.include_router(notifications_router)
api_router.include_router(offers_router)
