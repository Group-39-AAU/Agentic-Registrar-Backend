"""
Track C (Academic Standing) — Pydantic schemas.

PR C1 ships the read schemas powering the DH browse flow
(term → department → section → students). PR C2 will add the
compute / authorise / override request shapes.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.shared.enums import AcademicStatusType, GradeLetter


# ── Dropdown chain ─────────────────────────────────────────────


class StandingTermResponse(BaseModel):
    """
    One term as seen by the standing-roster browse flow. Every term
    is visible (open or closed); the officer typically picks a
    *closed* term whose grades have been authorised, but the API
    doesn't enforce that — a closed term with no grades just shows
    an empty roster, which is itself useful.
    """
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    term_name: str
    phase: str
    start_date: date
    end_date: date
    is_open: bool
    has_authorised_grades: bool


class StandingDepartmentResponse(BaseModel):
    """
    A department offering at least one Section in the chosen term.
    ``student_count`` is the number of registered students across
    every section in the (term, department) pair so the UI can hint
    at workload before drilling in.
    """
    department: str
    section_count: int
    student_count: int


class StandingSectionResponse(BaseModel):
    """
    One Section in the chosen (term, department). Cohort metadata is
    flattened so the UI can render the dropdown without follow-up
    lookups.
    """
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    section_code: str
    semester: int
    department: str
    enrolled_count: int
    capacity: int


# ── Per-student roster preview ──────────────────────────────────


class StudentTermGrade(BaseModel):
    """
    One authorised grade row for a student in the target term, with
    the add/drop context the officer needs to read the row correctly.
    Status filtering happens in the service — only rows visible here
    are ones that should count toward (or be flagged against) the
    student's SGPA for the term.
    """
    course_id: uuid.UUID
    course_code: str
    course_title: str
    credit_hours: int
    letter_grade: Optional[GradeLetter] = None
    numeric_score: Optional[float] = None
    grade_points: Optional[float] = None
    # True when the student dropped this course before the deadline
    # (RegistrationCourse.is_dropped). Surfaced so the officer can
    # see why a course they expected on the roster doesn't carry a
    # grade. Dropped courses do NOT contribute to SGPA.
    is_dropped: bool = False
    # True when the student picked up this course via an approved
    # add/drop batch — it's not part of their cohort's default
    # course list. Still counts toward SGPA when authorised.
    is_added_via_drop: bool = False


class ExistingStandingSummary(BaseModel):
    """
    Snapshot of any AcademicStanding row that already exists for the
    (student, term) pair. ``final_status`` is null until a DH has
    authorised. Surfaces in the roster so the UI can show "already
    decided" vs. "needs decision" at a glance.
    """
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    proposed_status: AcademicStatusType
    final_status: Optional[AcademicStatusType] = None
    requires_review: bool
    sgpa: Optional[float] = None
    cgpa: Optional[float] = None
    computed_at: datetime
    authorised_at: Optional[datetime] = None


class StudentStandingPreview(BaseModel):
    """
    One row of the officer roster view. The math here is computed
    **live** at request time from authorised grades in the ``grades``
    table — this is what the officer sees *before* the agent has run
    (or what gets re-confirmed each time the page is refreshed).

    The fields are aligned with the columns the
    :class:`AcademicStanding` row will eventually carry, so an
    officer comparing "live math" vs. "agent proposal" is comparing
    apples to apples.
    """
    student_id: uuid.UUID
    student_number: str
    full_name: str
    current_semester: int
    department: Optional[str] = None

    # Per-course grades in the target term (including dropped /
    # added context).
    grades_this_term: list[StudentTermGrade]

    # Live-computed math from authorised grades only.
    sgpa: Optional[float] = None
    cgpa: Optional[float] = None
    term_credit_hours: int
    cumulative_credit_hours: int
    f_count_term: int
    f_credit_total_term: int

    # Article-91 evaluation context, computed from term history.
    is_first_semester: bool
    is_first_year: bool
    has_incomplete_marks: bool

    # Linked AcademicStanding (if PR C2 has computed one yet).
    existing_standing: Optional[ExistingStandingSummary] = None


class StandingRosterResponse(BaseModel):
    """
    Full roster payload returned by the section-detail endpoint.
    Header fields name the cohort so the UI can render breadcrumbs.
    """
    term_id: uuid.UUID
    term_name: str
    section_id: uuid.UUID
    section_code: str
    department: str
    semester: int
    students: list[StudentStandingPreview]
