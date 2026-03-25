"""
Enrollment module — SQLAlchemy model.

Stores the official enrollment record generated when the
Enrollment & Onboarding Agent processes an admitted student.
"""

import uuid
from typing import Optional

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class Enrollment(Base):
    """
    Immutable enrollment record — one per admitted student.
    Contains the generated university ID, portal credentials,
    and assigned department / section.
    """
    __tablename__ = "enrollments"

    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("undergraduate_applications.id"),
        unique=True, nullable=False,
    )
    applicant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        index=True, nullable=False,
    )

    # ── University Credentials ──
    university_id: Mapped[str] = mapped_column(
        String(20), unique=True, index=True, nullable=False,
        comment="Format: UGR/XXXX/YY"
    )
    portal_password: Mapped[str] = mapped_column(
        String(255), nullable=False,
        comment="Temporary password — student must change on first login"
    )

    # ── Academic Assignment ──
    program_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academic_programs.id"),
        nullable=True,
    )
    department: Mapped[str] = mapped_column(String(100), nullable=False)
    section: Mapped[str] = mapped_column(
        String(10), nullable=False,
        comment="Auto-assigned section: A, B, C, …"
    )
    enrollment_term: Mapped[str] = mapped_column(String(50), nullable=False)
