"""
Track C — Records schemas.

Shapes for the grade-report and filing-slip endpoints. Both
documents are structured JSON: same fields a printed PDF would
carry, so frontend rendering is a 1:1 mapping.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel

from app.shared.enums import AcademicStatusType, GradeLetter, RegistrationStatus


# ── Shared header ──────────────────────────────────────────────


class RecordStudentHeader(BaseModel):
    """Identity panel printed on the top of every record."""
    student_id: uuid.UUID
    student_number: str
    full_name: str
    department: Optional[str] = None
    current_semester: int


class RecordTermHeader(BaseModel):
    """Term context panel."""
    term_id: uuid.UUID
    term_name: str
    term_phase: str
    term_start_date: date
    term_end_date: date


# ── Grade report (completed term) ──────────────────────────────


class GradeReportCourseLine(BaseModel):
    """One course row on a grade report."""
    course_code: str
    course_title: str
    credit_hours: int
    letter_grade: Optional[GradeLetter] = None
    numeric_score: Optional[float] = None
    grade_points: Optional[float] = None


class GradeReportResponse(BaseModel):
    """
    Official grade report for one completed term. Surfaces the
    DH-authorised AcademicStanding alongside the per-course grades.
    Returned only for terms whose standing has been authorised — the
    student's transcript covers the partial-data case.
    """
    document_id: uuid.UUID
    generated_at: datetime
    student: RecordStudentHeader
    term: RecordTermHeader
    courses: list[GradeReportCourseLine]
    term_gpa: Optional[float] = None
    cumulative_gpa: Optional[float] = None
    term_credit_hours: int
    cumulative_credit_hours: int
    academic_status: AcademicStatusType
    academic_status_authorised_at: datetime
    explanation: Optional[str] = None


# ── Filing slip (upcoming / in-progress term) ──────────────────


class FilingSlipCourseLine(BaseModel):
    """Course line on the filing slip — registered courses, no grades."""
    course_code: str
    course_title: str
    credit_hours: int
    is_dropped: bool = False


class FilingSlipResponse(BaseModel):
    """
    Filing slip = registration-support document for a term in
    progress (or upcoming). Confirms what the student is registered
    for + payment status + cohort section + carry-over standing
    from their last authorised term.
    """
    document_id: uuid.UUID
    generated_at: datetime
    student: RecordStudentHeader
    term: RecordTermHeader
    registration_status: RegistrationStatus
    sponsorship_type: str
    section_code: Optional[str] = None
    courses: list[FilingSlipCourseLine]
    total_credit_hours: int
    payment_reference: Optional[str] = None
    finalised_at: Optional[datetime] = None
    # Carry-over: the student's most recent authorised standing.
    last_authorised_status: Optional[AcademicStatusType] = None
    last_authorised_term_name: Optional[str] = None
