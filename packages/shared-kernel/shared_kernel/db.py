from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session


def utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""

    pass


UUID_PK = Annotated[uuid.UUID, mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)]


class TimestampMixin:
    """Mixin that adds created/updated timestamps."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )


class AuditLog(Base, TimestampMixin):
    """Durable audit log for important state changes."""

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    service_name: Mapped[str] = mapped_column(nullable=False)
    entity_type: Mapped[str] = mapped_column(nullable=False)
    entity_id: Mapped[str] = mapped_column(nullable=False)
    action: Mapped[str] = mapped_column(nullable=False)
    details: Mapped[str | None] = mapped_column(nullable=True)


def write_audit_event(
    session: Session,
    *,
    service_name: str,
    entity_type: str,
    entity_id: str,
    action: str,
    details: str | None = None,
) -> None:
    """Insert an audit log row in a durable table.

    This helper is intentionally minimal and can be extended with richer context later.
    """

    entry = AuditLog(
        service_name=service_name,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        details=details,
    )
    session.add(entry)

