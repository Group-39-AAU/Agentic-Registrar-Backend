from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from shared_kernel.logging import get_logger

from ..config.settings import get_settings

logger = get_logger(__name__)


def _create_engine():
    settings = get_settings()
    logger.info(
        "Creating notification-service database engine",
        extra={"url": settings.database_url},
    )
    return create_engine(settings.database_url, future=True)


Engine = _create_engine()
SessionLocal = sessionmaker(bind=Engine, autoflush=False, autocommit=False, future=True)


def get_db_session() -> Generator[Session, None, None]:
    """FastAPI dependency that provides a per-request DB session."""

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

