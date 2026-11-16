import os
from pathlib import Path
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Ensure importing the ASGI app in tests can never pick up a developer/production URL.
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["CORS_ORIGINS"] = "http://localhost:3000"
os.environ["JWT_SECRET"] = "test-only-signing-key-not-for-real-environments-12345"
os.environ["PASSWORD_MIN_LENGTH"] = "12"

from app.api.v1.auth import get_email_sender
from app.api.v1.applications import get_private_object_store
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import auth as auth_models  # noqa: F401  Register tables for test metadata.
from app.services.private_storage import LocalPrivateObjectStore


class TestEmailSender:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []

    def send(self, recipient: str, subject: str, body: str) -> None:
        self.messages.append((recipient, subject, body))


@pytest.fixture
def api_client(tmp_path: Path) -> Iterator[tuple[TestClient, sessionmaker, TestEmailSender]]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    mailer = TestEmailSender()

    def database_dependency():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = database_dependency
    app.dependency_overrides[get_email_sender] = lambda: mailer
    app.dependency_overrides[get_private_object_store] = lambda: LocalPrivateObjectStore(tmp_path / "private-documents")
    try:
        with TestClient(app) as client:
            yield client, factory, mailer
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()
