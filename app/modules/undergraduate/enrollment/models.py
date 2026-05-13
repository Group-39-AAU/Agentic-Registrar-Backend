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
    Carries only the durable academic-assignment fields the admission
    module produces. Section assignment moved to course-management
    (cohort sections per term, see :class:`Section`); portal password
    moved to the User row + ``must_change_password`` lockout.
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
    admission_term_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("undergraduate_admission_terms.id"),
        index=True,
        nullable=False,
    )

    # ── University Credentials ──
    university_id: Mapped[str] = mapped_column(
        String(20), unique=True, index=True, nullable=False,
        comment="Format: UGR/XXXX/YY"
    )

    # ── Academic Assignment ──
    program_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academic_programs.id"),
        nullable=True,
    )
    department: Mapped[str] = mapped_column(String(100), nullable=False)
    enrollment_term: Mapped[str] = mapped_column(String(50), nullable=False)
