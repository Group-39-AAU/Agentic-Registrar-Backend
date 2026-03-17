"""
MoE (Ministry of Education) module — Simulated student matric records.

This table acts as the Ministry's official database of 12th-grade
national exam results. The credential verification agent cross-checks
uploaded certificates against these records.
"""

from sqlalchemy import Float, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.shared.enums import StreamType


class MoeStudentRecord(Base):
    """
    Official matric result as the Ministry of Education would store it.
    Immutable — uses Base (no soft delete, no updates).
    """
    __tablename__ = "moe_student_records"

    admission_number: Mapped[str] = mapped_column(
        String(50), unique=True, index=True, nullable=False
    )
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    exam_year: Mapped[int] = mapped_column(Integer, nullable=False)
    stream: Mapped[StreamType] = mapped_column(nullable=False)
    subjects: Mapped[dict] = mapped_column(
        JSONB, nullable=False,
        comment='{"Physics": 85, "Math": 92, "English": 78, ...}'
    )
    total_score: Mapped[float] = mapped_column(Float, nullable=False)
