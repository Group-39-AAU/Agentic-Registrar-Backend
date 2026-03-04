"""
Programs module — AcademicProgram model.
"""

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import SoftDeleteBase


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
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
