"""
Track C (Academic Standing) — Pydantic schemas.

PR C1 ships the read schemas powering the DH browse flow
(term → department → section → students). PR C2 adds the
compute / authorise / override request + response shapes.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

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


# ── PR C2: compute / authorise / override ───────────────────────


class StandingComputeRequest(BaseModel):
    """
    Scope filter for the ``POST /terms/{tid}/compute`` endpoint.
    Both fields are optional — omitting both runs against every
    registered student in the term. Section_id alone is fine when
    the officer wants to reprocess a single cohort.
    """
    department: Optional[str] = None
    section_id: Optional[uuid.UUID] = None


class AcademicStandingResponse(BaseModel):
    """
    Full state of one AcademicStanding row. Mirrors every column on
    the model so the officer (and PR C3 student view) can render the
    Article-91 reasoning without follow-up queries.
    """
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    student_id: uuid.UUID
    term_id: uuid.UUID
    department: str

    sgpa: Optional[float] = None
    cgpa: Optional[float] = None
    term_credit_hours: int
    cumulative_credit_hours: int

    f_count_term: int
    f_credit_total_term: int
    is_first_semester: bool
    is_first_year: bool
    prior_status: Optional[AcademicStatusType] = None
    consecutive_warning_count: int

    proposed_status: AcademicStatusType
    final_status: Optional[AcademicStatusType] = None
    requires_review: bool

    computed_at: datetime
    computed_by_agent_id: Optional[str] = None
    authorised_by_id: Optional[uuid.UUID] = None
    authorised_at: Optional[datetime] = None
    override_reason: Optional[str] = None


class StandingComputeRow(BaseModel):
    """
    Per-student outcome of a compute run, with the rule trail.
    """
    standing: AcademicStandingResponse
    reasons: list[str]
    rule_citations: list[str]
    narrative: Optional[str] = None


class StandingComputeResponse(BaseModel):
    """
    Summary returned by ``POST /terms/{tid}/compute``. Counts
    grouped by proposed status so the officer sees the shape of the
    cohort at a glance before drilling into individual rows.
    """
    term_id: uuid.UUID
    computed_count: int
    skipped_count: int
    counts_by_status: dict[str, int]
    rows: list[StandingComputeRow]


class StandingAuthoriseRequest(BaseModel):
    """
    Body for ``POST /standing/{id}/authorise``. Reason is optional
    when the DH accepts the agent's proposal as-is; required only
    for overrides (see :class:`StandingOverrideRequest`).
    """
    reason: Optional[str] = None


class StandingOverrideRequest(BaseModel):
    """
    Body for ``POST /standing/{id}/override``. Both fields are
    required: the new status the DH wants to assign, and a written
    justification persisted in the audit trail.
    """
    new_status: AcademicStatusType
    reason: str = Field(min_length=10, max_length=4000)


class StandingQueueEntry(BaseModel):
    """
    Slim payload for a standing row — used by both the workflow
    queue (pending only) and the broader "list all" endpoint.
    Carries enough metadata for the DH to triage rows from a list
    page without fetching the full :class:`AcademicStandingResponse`.
    """
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    student_id: uuid.UUID
    student_number: str
    full_name: str
    term_id: uuid.UUID
    term_name: str
    department: str
    sgpa: Optional[float] = None
    cgpa: Optional[float] = None
    proposed_status: AcademicStatusType
    final_status: Optional[AcademicStatusType] = None
    requires_review: bool
    computed_at: datetime
    authorised_at: Optional[datetime] = None


# ── Batch authorise ────────────────────────────────────────────


class BatchAuthoriseRequest(BaseModel):
    """
    Body for ``POST /standing/batch-authorise``. A list of standing
    IDs the DH wants to authorise in one click. ``reason`` is the
    shared justification that applies to any held-for-review rows
    (rows with ``requires_review=True``). Clean proposals don't
    need a reason; if the DH is also clearing a few held-for-review
    rows in the same batch, the same ``reason`` covers them.

    Rows that are held-for-review without a written reason are
    surfaced as ``HELD_NEEDS_REASON`` outcomes and are skipped —
    the DH can return and authorise them individually with a more
    specific reason.
    """
    standing_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    reason: Optional[str] = Field(default=None, max_length=4000)


class BatchAuthoriseOutcome(BaseModel):
    """
    Per-id outcome of a batch authorise. Discriminator values:

      ``AUTHORISED``        — flipped to final_status=proposed_status
      ``ALREADY_AUTHORISED`` — final_status was already set; no-op
      ``HELD_NEEDS_REASON`` — requires_review=True with no reason;
                              skipped, DH must handle individually
      ``NOT_FOUND``         — standing_id didn't resolve to a row
    """
    standing_id: uuid.UUID
    status: str
    final_status: Optional[AcademicStatusType] = None
    message: Optional[str] = None


class BatchAuthoriseResponse(BaseModel):
    """
    Aggregated outcome of one batch authorise call. Counts let the
    UI show "X of Y authorised, Z need attention" without scanning
    the rows list.
    """
    requested_count: int
    authorised_count: int
    already_authorised_count: int
    held_needs_reason_count: int
    not_found_count: int
    rows: list[BatchAuthoriseOutcome]


# ── Student-facing (PR C3) ────────────────────────────────────


class StudentStandingResponse(BaseModel):
    """
    What a student sees when they look at their academic standing
    for one term. Only AUTHORISED rows surface — the agent's
    proposals and any pending decisions are invisible to students
    (same pattern as Track B's transcript filtering AUTHORISED-only
    grades).

    ``explanation`` is the plain-language reasoning shown alongside
    the status. Sourced as:
      1. ``override_reason`` if the DH wrote one (override path),
         otherwise
      2. the most recent ``AcademicStandingHistory.reason`` (the
         rules-engine sentence captured at PROPOSED / RE_COMPUTED
         time).
    """
    id: uuid.UUID
    term_id: uuid.UUID
    term_name: str
    term_phase: str
    term_start_date: date
    term_end_date: date
    status: AcademicStatusType
    sgpa: Optional[float] = None
    cgpa: Optional[float] = None
    term_credit_hours: int
    cumulative_credit_hours: int
    f_count_term: int
    authorised_at: Optional[datetime] = None
    explanation: Optional[str] = None


class StudentStandingTranscriptResponse(BaseModel):
    """
    Every authorised standing the calling student has, newest-first.
    Carries a derived ``current_status`` (the latest authorised
    status) for at-a-glance dashboard rendering.
    """
    student_id: uuid.UUID
    student_number: str
    full_name: str
    current_status: Optional[AcademicStatusType] = None
    terms: list[StudentStandingResponse]
