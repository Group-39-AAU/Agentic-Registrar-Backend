"""
SQLAlchemy declarative base and common model mixins.

Two base classes:
    - ``Base``           → For immutable ledgers (audit logs, status history).
                           Provides id (UUID), created_at, updated_at.
    - ``SoftDeleteBase`` → For core domain entities (applications, programs, documents).
                           Adds is_deleted flag for soft deletion.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """
    Abstract base for ALL models.
    Provides UUID primary key and timezone-aware UTC timestamps.
    """
    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SoftDeleteBase(Base):
    """
    Abstract base for core domain entities that support soft deletion.
    Adds ``is_deleted`` flag; repositories must filter is_deleted=False on reads.
    """
    __abstract__ = True

    is_deleted: Mapped[bool] = mapped_column(
        Boolean(),
        default=False,
        nullable=False,
    )
