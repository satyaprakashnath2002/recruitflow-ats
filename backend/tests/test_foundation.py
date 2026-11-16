from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Column, Integer, Table, create_engine
from sqlalchemy.exc import OperationalError
from alembic.autogenerate import produce_migrations
from alembic.migration import MigrationContext

from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app, create_app


class FailedDatabase:
    def execute(self, _statement: object) -> None:
        raise OperationalError("SELECT 1", {}, RuntimeError("database unavailable"))


def override_database(database: object):
    def dependency() -> Iterator[object]:
        yield database

    return dependency


def test_application_starts() -> None:
    application = create_app()
    with TestClient(application) as client:
        response = client.get("/openapi.json")
    assert response.status_code == 200
    assert "/api/v1/health" in response.json()["paths"]
    assert "/api/v1/auth/register" not in response.json()["paths"]


def test_liveness_returns_ok() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "recruitflow-api"}


def test_readiness_reports_database_available() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "available"}


def test_readiness_reports_database_failure_without_details() -> None:
    app.dependency_overrides[get_db] = override_database(FailedDatabase())
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/health/ready")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 503
    assert response.json() == {"error": {"status": "not_ready", "database": "unavailable"}}
    assert "database unavailable" not in response.text


def test_missing_database_configuration_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="database_url"):
        Settings(_env_file=None)


def test_invalid_pool_configuration_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("DB_POOL_SIZE", "0")
    with pytest.raises(ValueError, match="db_pool_size"):
        Settings(_env_file=None)


def test_notification_poll_interval_is_bounded() -> None:
    from pydantic import ValidationError

    common = {
        "database_url": "sqlite://",
        "jwt_secret": "test-only-signing-key-not-for-real-environments-12345",
        "app_env": "test",
    }
    assert Settings(**common).notification_poll_seconds == 30
    with pytest.raises(ValidationError, match="notification_poll_seconds"):
        Settings(**common, notification_poll_seconds=1)


def test_alembic_metadata_detects_future_table_changes() -> None:
    probe = Table("_autogenerate_probe", Base.metadata, Column("id", Integer, primary_key=True))
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as connection:
            migration_context = MigrationContext.configure(connection, opts={"target_metadata": Base.metadata})
            migration = produce_migrations(migration_context, Base.metadata)
        assert migration.upgrade_ops.ops
    finally:
        Base.metadata.remove(probe)
        engine.dispose()
