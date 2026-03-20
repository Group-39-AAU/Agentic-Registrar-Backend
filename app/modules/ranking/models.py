"""
Ranking module — SQLAlchemy models.

Contains:
    - StreamQuota    (admin-configurable capacity per stream for government-sponsored)
    - RankingResult  (output of each ranking batch run)
"""

import uuid
from typing import Optional

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, SoftDeleteBase
from app.shared.enums import StreamType


class StreamQuota(SoftDeleteBase):
    """
    Admin-configurable capacity per stream for government-sponsored students.
    Uses SoftDeleteBase so admins can manage quotas over time.
    """
    __tablename__ = "stream_quotas"

    stream: Mapped[StreamType] = mapped_column(unique=True, nullable=False, index=True)
    max_capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    admission_term: Mapped[str] = mapped_column(String(50), nullable=False, index=True)


class RankingResult(Base):
    """
    Stores per-applicant ranking output from a ranking batch.
    Immutable ledger — uses Base (no soft delete).
    """
    __tablename__ = "ranking_results"

    ranking_batch_id: Mapped[str] = mapped_column(
        String(50), index=True, nullable=False
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("undergraduate_applications.id"),
        nullable=False,
    )

    # ── Scores ──
    grade12_score: Mapped[float] = mapped_column(Float, nullable=False)
    uat_score: Mapped[float] = mapped_column(Float, nullable=False)
    final_score: Mapped[float] = mapped_column(Float, nullable=False)

    # ── Ranking ──
    category: Mapped[str] = mapped_column(String(20), nullable=False)  # SELF_SPONSORED or GOVERNMENT
    rank_position: Mapped[int] = mapped_column(Integer, nullable=False)

    # ── Assignment ──
    assigned_program_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academic_programs.id"),
        nullable=True,
    )
    assigned_stream: Mapped[Optional[StreamType]] = mapped_column(nullable=True)
    is_assigned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    assignment_detail: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )  # e.g. "Assigned to P1: Computer Science" or "Stream full"
