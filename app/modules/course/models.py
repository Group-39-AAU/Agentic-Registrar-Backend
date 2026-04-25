"""
Course Management — SQLAlchemy models.

The module follows the same layered pattern as undergraduate admission:

    models.py     — entities defined here
    schemas.py    — Pydantic request/response shapes
    repository.py — async CRUD
    service.py    — business logic + audit logging
    router.py     — API endpoints

Phase 0 entities (this file) are deliberately catalog-only:
AcademicTerm, Course, CoursePrerequisite, CourseOffering, Section,
Student, Instructor, InstructorAssignment, CourseManagementOfficer.

Workflow tables (Registration, Grades, Schedules, AcademicStanding,
ExceptionQueue, AcademicRecord) belong to Tracks A/B/C and land in
their respective PRs after this Phase 0 baseline merges.

Source-of-truth: SDS §3.1.3 Figure 5 (class diagram) and §5.3
Tables 55–84 (detailed design).
"""

import uuid
from datetime import date
from typing import Optional

from sqlalchemy import (
    Boolean, Date, ForeignKey, Integer, String, Text, UniqueConstraint,
    CheckConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, SoftDeleteBase


# ── Academic Calendar ────────────────────────────────────────────


class AcademicTerm(SoftDeleteBase):
    """
    A configurable academic term (semester) governing the Course
    Management lifecycle. The ``is_open`` flag is the gate the officer
    flips for the SRS Course-FR-01 "Course Registration Portal" use case
    — students cannot register against a term while it is closed.

    Tied to SDS state diagram (Figure 39) by being the parent of the
    Registration_Open / Add_Drop_Window calendar windows.
    """

    __tablename__ = "academic_terms"

    term_name: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_open: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


# ── Course Catalog ───────────────────────────────────────────────


class Course(SoftDeleteBase):
    """
    A catalog course (e.g. CS101 "Introduction to Programming").

    Independent of any academic term; per-term offerings are recorded
    on :class:`CourseOffering`. The ``credit_hours`` value drives the
    22-ECTS ceiling and 12-ECTS floor enforced by the Curriculum
    Compliance Agent (SDS Tables 65, 80).
    """

    __tablename__ = "courses"

    code: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    credit_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    semester: Mapped[int] = mapped_column(Integer, nullable=False)
    department: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "credit_hours BETWEEN 1 AND 12",
            name="ck_courses_credit_hours_range",
        ),
        CheckConstraint(
            "semester BETWEEN 1 AND 12",
            name="ck_courses_semester_range",
        ),
    )


class CoursePrerequisite(Base):
    """
    Self-referential mapping linking a course to its prerequisite
    courses. Powers ``CurriculumComplianceAgent.verifyPrerequisites``
    (SDS Table 66) — a registration is allowed only when every linked
    prerequisite has been passed with grade >= F.

    Inherits from :class:`Base` (no soft delete) because curriculum
    versions are append-only — a removed prerequisite stays in history.
    """

    __tablename__ = "course_prerequisites"

    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("courses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    prerequisite_course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("courses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    course: Mapped["Course"] = relationship(
        foreign_keys=[course_id], lazy="selectin"
    )
    prerequisite_course: Mapped["Course"] = relationship(
        foreign_keys=[prerequisite_course_id], lazy="selectin"
    )

    __table_args__ = (
        UniqueConstraint(
            "course_id", "prerequisite_course_id",
            name="uq_course_prereq_pair",
        ),
        CheckConstraint(
            "course_id <> prerequisite_course_id",
            name="ck_course_prereq_not_self",
        ),
    )
