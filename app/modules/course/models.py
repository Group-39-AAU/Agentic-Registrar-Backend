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

from datetime import date
from typing import Optional

from sqlalchemy import Boolean, Date, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import SoftDeleteBase


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
