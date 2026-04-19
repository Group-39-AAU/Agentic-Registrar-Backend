"""
Testing Center module — UAT Record model.

Stores Undergraduate Admission Test (UAT) records.
The UAT is a physical test taken off-system; this table tracks
the auto-generated UAT ID and the score received via callback.
"""

import uuid
from typing import Optional

from sqlalchemy import Boolean, Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class UATRecord(Base):
    """
    Tracks a student's UAT assignment and result.
    Uses Base (immutable ledger — no soft delete).
    """
    __tablename__ = "uat_records"

    uat_id: Mapped[str] = mapped_column(
        String(20), unique=True, index=True, nullable=False
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("undergraduate_applications.id"),
        nullable=False,
    )
    student_name: Mapped[str] = mapped_column(String(255), nullable=False)
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
