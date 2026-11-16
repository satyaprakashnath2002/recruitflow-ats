import logging
import asyncio
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.logging_config import configure_logging

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or get_settings()
    configure_logging(config.log_level)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        logger.info("Starting %s in %s environment", config.app_name, config.app_env)
        worker = None if config.app_env == "test" else asyncio.create_task(_notification_worker(config.notification_poll_seconds))
        yield
        if worker is not None:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
        logger.info("Stopping %s", config.app_name)

    application = FastAPI(title=config.app_name, version="0.1.0", lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origin_list,
        allow_credentials=bool(config.cors_origin_list),
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Offer-Capability"],
    )
    application.include_router(api_router, prefix="/api/v1")

    @application.exception_handler(HTTPException)
    async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
        detail: Any = exc.detail
        error = detail if isinstance(detail, dict) and "status" in detail else {
            "status": "error",
            "message": str(detail),
        }
        return JSONResponse(status_code=exc.status_code, content={"error": error}, headers=exc.headers)

    @application.exception_handler(RequestValidationError)
    async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "status": "error",
                    "message": "Invalid request",
                    "details": [
                        {key: value for key, value in error.items() if key in {"loc", "msg", "type"}}
                        for error in jsonable_encoder(exc.errors())
                    ],
                }
            },
        )

    @application.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled request error on %s", request.url.path, exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={"error": {"status": "error", "message": "Internal server error"}},
        )

    return application


async def _notification_worker(poll_seconds: int) -> None:
    """Poll persistent due work off the ASGI event loop; DB locks deduplicate instances."""
    from app.db.session import SessionLocal
    from app.services.notifications import process_due_reminders
    from app.services.offers import process_due_offer_reminders

    while True:
        await asyncio.sleep(poll_seconds)
        try:
            def process_batch() -> tuple[int, int]:
                with SessionLocal() as db:
                    interview_count = process_due_reminders(db)
                with SessionLocal() as db:
                    offer_count = process_due_offer_reminders(db)
                return interview_count, offer_count

            await asyncio.to_thread(process_batch)
        except Exception:
            logger.exception("Notification reminder polling failed; pending reminders remain retryable")


app = create_app()
