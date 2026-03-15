from __future__ import annotations

from collections.abc import Generator
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from shared_kernel.db import Base


def create_sqlite_memory_engine(echo: bool = False):
    """Create an in-memory SQLite engine for testing."""

    return create_engine("sqlite+pysqlite:///:memory:", echo=echo, future=True)


def create_test_session_factory(echo: bool = False) -> sessionmaker[Session]:
    """Create a session factory bound to an in-memory SQLite database."""

    engine = create_sqlite_memory_engine(echo=echo)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session, future=True)


def session_scope(SessionFactory: sessionmaker[Session]) -> Generator[Session, None, None]:
    """Context manager-like helper for managing a SQLAlchemy session."""

    session = SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

