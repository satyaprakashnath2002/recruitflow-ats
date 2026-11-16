from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings


def create_database_engine(settings: Settings | None = None) -> Engine:
    config = settings or get_settings()
    url = config.database_url.get_secret_value()
    options: dict[str, object] = {"pool_pre_ping": True}
    connect_args: dict[str, object] = {}

    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        if url in {"sqlite://", "sqlite:///:memory:"}:
            from sqlalchemy.pool import StaticPool

            options["poolclass"] = StaticPool
    else:
        options.update(
            pool_size=config.db_pool_size,
            max_overflow=config.db_max_overflow,
            pool_timeout=config.db_pool_timeout,
            pool_recycle=config.db_pool_recycle,
        )
        connect_args["connect_timeout"] = config.db_pool_timeout

    return create_engine(url, connect_args=connect_args, **options)


engine = create_database_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
