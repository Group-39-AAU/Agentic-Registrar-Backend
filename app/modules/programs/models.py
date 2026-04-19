"""
Programs module — AcademicProgram model.
"""

from typing import Optional

from sqlalchemy import Boolean, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import SoftDeleteBase
from app.shared.enums import StreamType


class AcademicProgram(SoftDeleteBase):
    """
    Official degrees / majors offered by the university.
    Uses SoftDeleteBase (institutional entity — never hard-deleted).
    """
    __tablename__ = "academic_programs"

    code: Mapped[str] = mapped_column(
        String(50), unique=True, index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    department: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    stream: Mapped[StreamType] = mapped_column(nullable=False, index=True)
    cut_off_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_capacity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
