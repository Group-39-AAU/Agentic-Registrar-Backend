"""
System Audit Log — immutable governance ledger.
"""

import uuid
from typing import Optional

from sqlalchemy import JSON, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class SystemAuditLog(Base):
    """
    Universal compliance logging for the entire platform.
    Inherits from Base — immutable, NO updates, NO deletes.
    Deliberately decoupled from FK constraints to guarantee log immutability
    even if related records are soft-deleted.
    """
    __tablename__ = "system_audit_logs"

    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    actor_role: Mapped[str] = mapped_column(String(50), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str] = mapped_column(
        String(100), index=True, nullable=False
    )
    resource_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), index=True, nullable=False
    )
    decision: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # Cross-DB type: JSONB on Postgres (production), JSON on SQLite (tests).
    # The Postgres column type is unchanged — only the Python-side type is
    # widened so SQLAlchemy can also emit a SQLite-compatible CREATE TABLE.
    metadata_payload: Mapped[dict] = mapped_column(
        "metadata",
        JSON().with_variant(JSONB(), "postgresql"),
        server_default="{}",
        nullable=False,
    )
