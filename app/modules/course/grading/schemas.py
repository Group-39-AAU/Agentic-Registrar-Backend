"""
Track B (Grading) — Pydantic request/response shapes.

PR 1 surface: the two instructor reads.

  * InstructorSectionAssignmentResponse — one entry per (section,
    course) pair the calling instructor teaches in a given term.
  * SectionCourseRosterResponse        — effective roster of students
    for a (section, course) pair, with each student tagged as
    ORIGINAL (registered into the section's cohort) or ADDED (joined
    this section's slot via an approved add/drop batch).

PR 2 surface: breakdown editor + grade-entry batch.

  * AssessmentComponentCreate / Response  — one weighted row of the
    breakdown.
  * AssessmentBreakdownCreate / Response  — POST body and read view
    of a (section, course) breakdown. The sum-of-weights == 100
    invariant is validated server-side.
  * GradeBatchResponse                    — read view of the workflow
    object, including the current score matrix and per-student
    completion status.
  * StudentScoreCellResponse              — one cell of the score
    matrix (one component × one student).
  * ScoreCellWrite / BulkScoreWrite       — PUT body for upserting
    one or many score cells in a single round trip.
  * GradeBatchSubmitResponse              — what comes back from
    submit (verdict + per-student computed letter + numeric).

Later PRs append agent-review and authorisation shapes here.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.shared.enums import GradeLetter, GradeSubmissionStatus


# ══════════════════════════════════════════════════════════════
#  Instructor — "sections I teach"
# ══════════════════════════════════════════════════════════════


class InstructorSectionAssignmentResponse(BaseModel):
    """
    One (section, course) pair the calling instructor teaches in a
    term. The grading unit is the *pair*, not just the section — a
    section serves multiple courses simultaneously and each is graded
    independently.

    Derived from the ClassScheduleSlot rows where the slot's
    ``instructor_id`` matches the caller. Deduplicated to one entry
    per (section_id, course_id).
    """
    model_config = ConfigDict(from_attributes=True)

    section_id: uuid.UUID
    section_code: str
    section_department: str
    section_semester: int
    course_id: uuid.UUID
    course_code: str
    course_title: str
    course_credit_hours: int
    term_id: uuid.UUID
    # How many distinct weekly slots the instructor has for this
    # (section, course) pair — useful for the UI to surface "you
    # teach 3 hours/week of CS101 in Section A".
    slot_count: int


# ══════════════════════════════════════════════════════════════
#  Roster — effective members of a (section, course)
# ══════════════════════════════════════════════════════════════


class RosterStudentResponse(BaseModel):
    """
    One row in the effective roster for a (section, course) pair.

    ``is_added_via_drop=True`` distinguishes students who joined this
    section through an approved add/drop batch (sourced from
    ``StudentScheduleAddition``) from students whose original cohort
    *is* this section (``Registration.section_id == section_id``).

    Students who originally registered for the course but dropped it
    (``RegistrationCourse.is_dropped == True``) and students who
    transferred *out* via add/drop to a different section are
    excluded entirely — they do not appear here at all.
    """
    model_config = ConfigDict(from_attributes=True)

    student_id: uuid.UUID
    student_number: str  # AAU "UGR/XXXX/YY" identifier
    full_name: str
    current_semester: int
    registration_id: uuid.UUID
    is_added_via_drop: bool


class SectionCourseRosterResponse(BaseModel):
    """
    Roster payload for a (section, course) pair. The summary counts
    are computed server-side so the UI doesn't have to re-tally them.
    """
    section_id: uuid.UUID
    section_code: str
    course_id: uuid.UUID
    course_code: str
    course_title: str
    term_id: uuid.UUID

    total: int
    original_count: int
    added_count: int

    students: list[RosterStudentResponse]


# ══════════════════════════════════════════════════════════════
#  Assessment breakdown — PR 2
# ══════════════════════════════════════════════════════════════


class AssessmentComponentCreate(BaseModel):
    """
    One row of the POSTed breakdown body.

    ``max_score`` defaults to the component's ``weight`` when omitted:
    if an instructor says "Quiz weight 10", we assume they grade
    the quiz out of 10 too, so the raw score IS the weighted
    contribution (no mental arithmetic, no scaling). When the two
    scales differ (e.g. "Mid out of 50, worth 30%") the instructor
    sets ``max_score`` explicitly.
    """
    name: str = Field(min_length=1, max_length=120)
    weight: float = Field(gt=0, le=100)
    # Optional in the payload; resolved to ``weight`` by the validator
    # below before the service ever sees it. Must be positive when
    # provided.
    max_score: Optional[float] = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _default_max_score_to_weight(self) -> "AssessmentComponentCreate":
        if self.max_score is None:
            # Pydantic models are frozen against __setattr__ only when
            # ``frozen=True``; ours isn't, so a direct assignment is fine.
            self.max_score = self.weight
        return self


class AssessmentBreakdownCreate(BaseModel):
    """
    POST body for ``/sections/{sid}/courses/{cid}/breakdown``. The
    service rejects any payload whose ``sum(components.weight) != 100``
    (within floating-point tolerance) — this is the policy invariant
    from the Track B checklist (lines 121, 156).
    """
    components: list[AssessmentComponentCreate] = Field(
        min_length=1,
        description="At least one component; weights must sum to 100.",
    )


class AssessmentComponentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    weight: float
    max_score: float
    order_index: int


class AssessmentBreakdownResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    section_id: uuid.UUID
    course_id: uuid.UUID
    instructor_id: uuid.UUID
    term_id: uuid.UUID
    version: int
    locked_at: Optional[datetime]
    components: list[AssessmentComponentResponse]


# ══════════════════════════════════════════════════════════════
#  Grade batch — PR 2
# ══════════════════════════════════════════════════════════════


class StudentScoreCellResponse(BaseModel):
    """One cell of the (student × component) score matrix."""
    model_config = ConfigDict(from_attributes=True)

    student_id: uuid.UUID
    component_id: uuid.UUID
    score: Optional[float]


class StudentBatchRowResponse(BaseModel):
    """
    Per-student aggregate row for the UI: one student's full score
    set for the batch, plus a ``is_complete`` flag indicating whether
    every component has a non-null score.
    """
    student_id: uuid.UUID
    student_number: str
    full_name: str
    is_added_via_drop: bool
    is_complete: bool
    scores: list[StudentScoreCellResponse]


class GradeBatchResponse(BaseModel):
    """Read view of one grade batch, including the live score matrix."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    section_id: uuid.UUID
    section_code: str
    course_id: uuid.UUID
    course_code: str
    course_title: str
    course_credit_hours: int
    term_id: uuid.UUID
    instructor_id: uuid.UUID
    breakdown_id: uuid.UUID
    status: GradeSubmissionStatus
    iteration_count: int
    submitted_at: Optional[datetime]
    instructor_justification: Optional[str]

    breakdown: AssessmentBreakdownResponse
    rows: list[StudentBatchRowResponse]


# ── Score upsert ──


class ScoreCellWrite(BaseModel):
    """One score cell in a bulk-upsert payload."""
    student_id: uuid.UUID
    component_id: uuid.UUID
    # Nullable so the instructor can "blank out" a cell back to
    # unentered. Negative scores rejected.
    score: Optional[float] = Field(default=None, ge=0)


class BulkScoreWrite(BaseModel):
    """
    PUT body for ``/batches/{bid}/scores``. The service validates
    each cell against the component's ``max_score`` and the roster
    membership (no scores for students who aren't on the roster).
    """
    cells: list[ScoreCellWrite] = Field(min_length=1)


# ── Submit ──


class SubmittedGradeRow(BaseModel):
    """Per-student outcome after the submit-time weighted computation."""
    student_id: uuid.UUID
    student_number: str
    full_name: str
    numeric_score: float
    letter_grade: GradeLetter


class GradeBatchSubmitResponse(BaseModel):
    """
    Reply to ``POST /batches/{bid}/submit``. Carries the new batch
    status, the computed per-student outcomes, and the agent's
    verdict.

    ``agent_verdict`` is ``APPROVE`` / ``FLAG`` / ``PENDING``;
    ``PENDING`` means the LLM call failed and the agent needs a
    manual re-trigger (PR 4 endpoint). The batch stays at SUBMITTED
    in that case; nothing is deadlocked.
    """
    batch_id: uuid.UUID
    status: GradeSubmissionStatus
    iteration: int
    submitted_at: datetime
    agent_verdict: Literal["APPROVE", "FLAG", "PENDING"]
    agent_flags: list[dict] = Field(default_factory=list)
    agent_reasoning: str
    grades: list[SubmittedGradeRow]


# ══════════════════════════════════════════════════════════════
#  Agent review history (PR 3 — DH/instructor read)
# ══════════════════════════════════════════════════════════════


class GradeAgentReviewResponse(BaseModel):
    """One row of the append-only agent-review history for a batch."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    iteration: int
    verdict: Literal["APPROVE", "FLAG", "PENDING"]
    flags: list[dict]
    llm_reasoning: Optional[str]
    tool_findings: dict
    agent_id: str
    created_at: datetime


# ══════════════════════════════════════════════════════════════
#  Justify + reopen (PR 3 — instructor iteration loop)
# ══════════════════════════════════════════════════════════════


class InstructorJustificationRequest(BaseModel):
    """
    POST body for ``/batches/{bid}/justify``. The instructor explains
    in writing why the agent's prior flags are unfounded. The agent
    is re-run with the justification appended to its context and may
    APPROVE on iteration 2+.
    """
    justification: str = Field(min_length=10, max_length=4000)


# ══════════════════════════════════════════════════════════════
#  PR 4 — Department-head workflow
# ══════════════════════════════════════════════════════════════


class QueueDepartmentOption(BaseModel):
    """
    One option for the DH queue's department-filter dropdown. Only
    departments with at least one batch awaiting a decision appear,
    so the dropdown can never select a value that yields an empty
    queue. ``pending_count`` lets the UI show "Computer Science (3)".
    """
    department: str
    pending_count: int


class DepartmentHeadQueueEntry(BaseModel):
    """
    Compact summary for the DH review queue. One entry per
    ``GradeBatch`` awaiting a DH decision (status SUBMITTED or
    FLAGGED). Ordered by ``submitted_at`` ascending so the oldest
    batch surfaces first.
    """
    model_config = ConfigDict(from_attributes=True)

    batch_id: uuid.UUID
    section_id: uuid.UUID
    section_code: str
    section_department: str
    section_semester: int
    course_id: uuid.UUID
    course_code: str
    course_title: str
    term_id: uuid.UUID
    term_name: str
    instructor_id: uuid.UUID
    instructor_name: str
    status: GradeSubmissionStatus
    iteration_count: int
    submitted_at: Optional[datetime]
    # Convenience: the latest agent verdict for the batch ("APPROVE",
    # "FLAG", "PENDING", or None if no agent has run yet).
    latest_agent_verdict: Optional[Literal["APPROVE", "FLAG", "PENDING"]]
    flag_count: int  # number of flags in the latest agent review
    has_instructor_justification: bool
    roster_total: int


class GradeAuthorisationDecisionResponse(BaseModel):
    """One immutable row of the DH decision audit trail."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    iteration: int
    decision: Literal[
        "AUTHORISED", "REJECTED",
        "OVERRODE_AGENT_APPROVAL", "OVERRODE_AGENT_FLAG",
    ]
    department_head_id: uuid.UUID
    decision_at: datetime
    justification: Optional[str]


class GradeBatchReviewPacketResponse(BaseModel):
    """
    Full DH review packet for one batch. Combines the live batch
    view (breakdown + score matrix + per-student computed letters)
    with every agent run and every DH decision so the DH has the
    complete history in one response.
    """
    batch: GradeBatchResponse
    per_student_grades: list["SubmittedGradeRow"]
    agent_reviews: list[GradeAgentReviewResponse]
    decisions: list[GradeAuthorisationDecisionResponse]


class DepartmentHeadDecisionRequest(BaseModel):
    """
    POST body for ``/officer/batches/{bid}/authorise`` and
    ``/officer/batches/{bid}/reject``.

    ``justification`` is required for everything except accepting a
    clean agent APPROVE (the only "no reason needed" path). Service
    layer enforces the per-decision rule; this schema just gives the
    field an upper bound when present.
    """
    justification: Optional[str] = Field(
        default=None, max_length=4000,
    )


class DepartmentHeadDecisionResponse(BaseModel):
    """Reply from authorise / reject — the new batch state + the audit row."""
    batch_id: uuid.UUID
    new_status: GradeSubmissionStatus
    decision: Literal[
        "AUTHORISED", "REJECTED",
        "OVERRODE_AGENT_APPROVAL", "OVERRODE_AGENT_FLAG",
    ]
    decision_id: uuid.UUID
    decision_at: datetime
    department_head_id: uuid.UUID


class AgentRerunResponse(BaseModel):
    """
    Reply from ``POST /officer/batches/{bid}/rerun-agent``. Carries
    the new verdict + reasoning if the LLM came back, or PENDING if
    it's still unavailable.
    """
    batch_id: uuid.UUID
    new_status: GradeSubmissionStatus
    iteration: int
    agent_verdict: Literal["APPROVE", "FLAG", "PENDING"]
    agent_flags: list[dict]
    agent_reasoning: str


# ══════════════════════════════════════════════════════════════
#  PR 4 — Student transcript
# ══════════════════════════════════════════════════════════════


class TranscriptComponentScore(BaseModel):
    """One component contribution to a course grade in the transcript."""
    name: str
    weight: float
    max_score: float
    score: Optional[float]
    weighted_contribution: Optional[float]


class TranscriptCourseEntry(BaseModel):
    """
    One AUTHORISED grade in a student's transcript. ``has_breakdown``
    is False for legacy ``Grade`` rows that pre-date Track B (no
    associated ``GradeBatch``) — for those, the components list is
    empty but the letter and numeric are still surfaced.
    """
    course_id: uuid.UUID
    course_code: str
    course_title: str
    credit_hours: int
    letter_grade: GradeLetter
    numeric_score: Optional[float]
    grade_points: Optional[float]
    has_breakdown: bool
    components: list[TranscriptComponentScore]


class TranscriptTermEntry(BaseModel):
    """All AUTHORISED grades for one term, plus the per-term GPA."""
    term_id: uuid.UUID
    term_name: str
    term_phase: str
    term_start_date: date
    term_end_date: date
    courses: list[TranscriptCourseEntry]
    term_gpa: Optional[float]   # weighted average of grade_points / credit_hours
    total_credit_hours: int


class TranscriptResponse(BaseModel):
    """The student-facing transcript — every term grouped, with CGPA."""
    student_id: uuid.UUID
    student_number: str
    full_name: str
    terms: list[TranscriptTermEntry]
    cgpa: Optional[float]
    total_credit_hours_completed: int

