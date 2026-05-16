"""
Course Management — Pydantic request/response schemas.

Track A foundation set: registration draft + submit + officer
window control. Tracks A.3–A.5 will append schemas for scheduling,
add/drop, and advisory in their respective PRs.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.shared.enums import (
    AcademicPhase, AddDropAction, AddDropRequestStatus, ConsultationMode, EnrollmentStatus,
    RegistrationStatus, RiskStatus, SponsorshipType,
)


# ══════════════════════════════════════════════════════════════
#  AcademicTerm — officer window control
# ══════════════════════════════════════════════════════════════


class AcademicTermResponse(BaseModel):
    """Read view of an AcademicTerm."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    term_name: str
    phase: AcademicPhase
    start_date: date
    end_date: date
    is_open: bool
    description: Optional[str] = None


# ══════════════════════════════════════════════════════════════
#  Course (catalog read for registration portal)
# ══════════════════════════════════════════════════════════════


class CourseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    title: str
    credit_hours: int
    semester: int
    department: str


class AvailableCoursesRequest(BaseModel):
    """
    Body for ``POST /courses/me/available-courses``. The student
    picks the term they want to inspect; the service then decides
    whether to show their registered selection or the curriculum
    picker based on whether they already have a Registration for
    that term.
    """
    term_id: uuid.UUID


class AvailableCoursesResponse(BaseModel):
    """
    200-only payload from ``POST /me/available-courses``. Two shapes,
    keyed off whether a Registration exists for the (student, term)
    pair (errors are surfaced as 404 / 409 instead of returned here):

      * Has a Registration → ``is_registered=True``,
        ``registration_id`` + ``registration_status`` populated, and
        ``courses`` is the active (non-dropped) registered selection.
        Frontends drive the action button off ``registration_status``:
        ``REGISTRATION_OPEN`` → still in draft, ``PAYMENT_HOLD`` →
        prompt to pay, ``REGISTERED`` → show "Registered ✓", etc.
      * No Registration (only reachable when the term is OPEN) →
        ``is_registered=False`` and ``courses`` is the curriculum
        picker the student will submit from.
    """
    term: AcademicTermResponse
    is_registered: bool
    registration_id: Optional[uuid.UUID] = None
    registration_status: Optional[RegistrationStatus] = None
    courses: list[CourseResponse]


# ══════════════════════════════════════════════════════════════
#  Registration
# ══════════════════════════════════════════════════════════════


class SelectCoursesAndSubmitRequest(BaseModel):
    """
    Request: student picks the exact set of courses they want to take
    in a term and submits — all in one call. The service finds (or
    creates) the term's draft registration, reconciles its course
    list to ``course_ids`` exactly, then runs the compliance pipeline.
    """
    term_id: uuid.UUID
    course_ids: list[uuid.UUID] = Field(default_factory=list)


class RegistrationPaymentInitiateResponse(BaseModel):
    """Response from /payment/initiate — what a real gateway page would consume."""
    registration_id: uuid.UUID
    payment_reference: str
    payment_url: str


class RegistrationPaymentCallbackRequest(BaseModel):
    """
    Body for the simulated bursar-gateway callback. In production this
    would carry the gateway's signed payload; in the mock we just echo
    back the payment_reference returned by /initiate.
    """
    payment_reference: str = Field(..., min_length=1, max_length=64)


class InvoiceLineItem(BaseModel):
    """One course's contribution to the tuition invoice."""
    course_id: uuid.UUID
    course_code: str
    course_title: str
    credit_hours: int
    line_total: int


class RegistrationInvoiceResponse(BaseModel):
    """
    Per-credit-hour tuition breakdown for a registration.

    Self-sponsored students see ``amount_due == gross_total``;
    government-sponsored students see ``amount_due == 0`` and the
    same line items for transparency. ``note`` is a human-readable
    summary the portal can show verbatim.
    """
    registration_id: uuid.UUID
    sponsorship_type: SponsorshipType
    currency: str
    fee_per_credit_hour: int
    lines: list[InvoiceLineItem] = Field(default_factory=list)
    total_credit_hours: int
    gross_total: int
    amount_due: int
    is_government_sponsored: bool
    payment_required: bool
    note: str


class CostSharingFormResponse(BaseModel):
    """
    Response from POST /registrations/{id}/cost-sharing-form.
    Government-sponsored students sign a single form covering every
    course they registered for; the response echoes which courses
    were marked as covered so the portal can confirm coverage to
    the student.
    """
    registration_id: uuid.UUID
    sponsorship_type: str
    course_count: int
    marked_paid_course_ids: list[uuid.UUID] = Field(default_factory=list)


class RegistrationCourseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    course_id: uuid.UUID
    section_id: Optional[uuid.UUID] = None
    is_dropped: bool


class RegistrationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    student_id: uuid.UUID
    term_id: uuid.UUID
    status: RegistrationStatus
    sponsorship_type: SponsorshipType
    payment_reference: Optional[str] = None
    finalised_at: Optional[datetime] = None
    courses: list[RegistrationCourseRead] = Field(default_factory=list)


# ══════════════════════════════════════════════════════════════
#  Compliance result (returned by /submit)
# ══════════════════════════════════════════════════════════════


class ComplianceResultResponse(BaseModel):
    """
    Pass-through of the CurriculumComplianceAgent.process_task
    payload. Shape matches that dict so the router can return it
    directly without massaging.
    """
    overall_passed: bool
    prereq_results: list[dict]
    load_result: dict
    payment_result: dict


class RegistrationSubmitResponse(BaseModel):
    """Bundles the post-submit registration with the agent verdict."""
    registration: RegistrationResponse
    compliance: ComplianceResultResponse


# ══════════════════════════════════════════════════════════════
#  Scheduling — officer trigger + read views
# ══════════════════════════════════════════════════════════════


class ScheduleGenerateRequest(BaseModel):
    """
    Officer payload for the scheduling officer endpoints. Scheduling
    is one department at a time — pass the term + the
    :class:`AcademicProgram` UUID of the owning department, and the
    agent allocates cohorts (semesters 1-10 within that department)
    and emits ClassScheduleSlot rows. Other departments are untouched.
    """
    term_id: uuid.UUID
    program_id: uuid.UUID


class SectionRead(BaseModel):
    """Cohort section header (without slots). Rooms live on each
    ``ClassScheduleSlot`` now — a cohort can attend different courses
    in different classrooms throughout the week."""
    section_id: uuid.UUID
    section_code: str
    department: str
    semester: int
    capacity: int
    enrolled_count: int


class ClassScheduleSlotRead(BaseModel):
    """One weekly meeting in a section's schedule."""
    course_code: str
    course_title: str
    day_of_week: str
    start_time: str
    end_time: str
    instructor_id: Optional[uuid.UUID] = None
    room: Optional[str] = None


class SectionScheduleResponse(BaseModel):
    """
    Schedule view for a single section. Returned by /me/schedule (after
    resolving the caller's section), /students/{id}/schedule, and
    /sections/{id}/schedule. ``section`` is null when the caller has no
    cohort assignment yet (e.g. officer hasn't run scheduling).
    """
    term_id: uuid.UUID
    student_id: Optional[uuid.UUID] = None
    section: Optional[SectionRead] = None
    slots: list[ClassScheduleSlotRead] = Field(default_factory=list)


class InstructorScheduleEntry(BaseModel):
    """One row in an instructor's per-term schedule view. ``room``
    reflects the specific weekly meeting's classroom — different
    meetings of the same section may use different rooms."""
    section_id: uuid.UUID
    section_code: str
    department: str
    semester: int
    room: Optional[str] = None
    course_code: str
    course_title: str
    day_of_week: str
    start_time: str
    end_time: str


class AssignInstructorToSlotRequest(BaseModel):
    """
    Officer payload for ``POST /officer/schedule/slots/{slot_id}/
    assign-instructor`` — picks the new instructor to teach the slot.
    """
    instructor_id: uuid.UUID


class SlotInstructorAssignmentResponse(BaseModel):
    """Returned after an officer successfully reassigns a slot's instructor."""
    slot_id: uuid.UUID
    section_id: uuid.UUID
    course_id: uuid.UUID
    instructor_id: uuid.UUID
    day_of_week: str
    start_time: str
    end_time: str


class ScheduleConflictRead(BaseModel):
    """Conflict-report row exposed to the officer."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    term_id: uuid.UUID
    department: str
    conflict_type: str
    section_id: Optional[uuid.UUID] = None
    other_section_id: Optional[uuid.UUID] = None
    instructor_id: Optional[uuid.UUID] = None
    time_slot: Optional[str] = None
    room: Optional[str] = None
    description: str
    status: str
    detected_by_agent_id: str


class SectionAllocationResponse(BaseModel):
    """
    Payload returned from ``POST /officer/sections/allocate`` — phase
    1 of scheduling. Every REGISTERED student in the department now
    has a ``Registration.section_id``; per-class weekly slots are
    emitted by the separate :class:`TimetableGenerateResponse` flow.
    """
    term_id: str
    department: str
    sections_created: list[dict] = Field(default_factory=list)
    students_placed_count: int
    students_placed: list[dict] = Field(default_factory=list)
    failed: list[dict] = Field(default_factory=list)


class TimetableGenerateResponse(BaseModel):
    """
    Payload returned from ``POST /officer/schedule/generate`` — phase
    2 of scheduling. The department's sections already exist (from
    the section-allocation call); this response carries the per-
    section weekly slots that were just emitted.
    """
    term_id: str
    department: str
    section_count: int
    slots_created: int
    sections: list[dict] = Field(default_factory=list)
    conflict_count: int
    conflict_ids: list[str] = Field(default_factory=list)


# ══════════════════════════════════════════════════════════════
#  Add/Drop
# ══════════════════════════════════════════════════════════════


class AddDropRequestCreate(BaseModel):
    """Request: student submits an add/drop change."""
    registration_id: uuid.UUID
    course_id: uuid.UUID
    action: AddDropAction
    deadline: date


class AddDropRequestResponse(BaseModel):
    """Read view of an AddDropRequest."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    registration_id: uuid.UUID
    course_id: uuid.UUID
    action: AddDropAction
    deadline_snapshot: date
    status: AddDropRequestStatus
    reason: Optional[str] = None
    override_by_id: Optional[uuid.UUID] = None
    override_justification: Optional[str] = None


class AddDropOverrideRequest(BaseModel):
    """Officer override payload."""
    justification: str = Field(..., min_length=3, max_length=4000)


# ══════════════════════════════════════════════════════════════
#  Advisory
# ══════════════════════════════════════════════════════════════


class AdvisoryEvaluateRequest(BaseModel):
    """
    Student payload for ``POST /advisory/evaluate``.

    Phase-1 note: ``cgpa`` and ``completed_course_ids`` come from
    the caller because there is no Grade model yet. When Track B
    lands these fields will become server-resolved.
    """
    term_id: uuid.UUID
    proposed_course_ids: list[uuid.UUID] = Field(default_factory=list)
    cgpa: float = Field(..., ge=0.0, le=4.0)
    completed_course_ids: list[uuid.UUID] = Field(default_factory=list)


class AdvisoryRecommendationRead(BaseModel):
    """Read view of an AdvisoryRecommendation row."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    student_id: uuid.UUID
    term_id: uuid.UUID
    risk_status: RiskStatus
    risk_explanation: str
    proposed_courses: list = Field(default_factory=list)
    recommended_courses: list = Field(default_factory=list)
    gap_analysis: dict = Field(default_factory=dict)
    consultation_mode: Optional[ConsultationMode] = None
    graduation_impact: Optional[dict] = None
    requires_officer_review: bool
    reviewed_by_id: Optional[uuid.UUID] = None
    reviewed_at: Optional[datetime] = None
    review_notes: Optional[str] = None


class AdvisoryReviewCloseRequest(BaseModel):
    """Officer payload for closing an advisory HITL review."""
    review_notes: str = Field(..., min_length=3, max_length=4000)


# ══════════════════════════════════════════════════════════════
#  Advisory — demand-driven LLM consultations
# ══════════════════════════════════════════════════════════════
#
# These three endpoints power the student's "Ask the Advisor" UX
# from three distinct moments in the registration lifecycle:
#
#   POST /advisory/consult/pre-registration  (no draft yet)
#   POST /advisory/consult/registration-plan (draft ready, validate)
#   POST /advisory/consult/add-drop          (mid-term changes)
#
# Each shares the AcademicHistoryOverride mixin: until Track B
# (grades) lands, the caller must supply CGPA + completed-course
# IDs because the server cannot resolve them. Both fields are
# required for now to keep the LLM payload faithful.


# The pre-registration and registration-plan consult endpoints take
# NO request body — every input (student, department, current
# semester, CGPA, completed courses, current term, in-progress
# registration draft) is server-resolved. The router exposes those
# endpoints with no payload parameter.

class AdvisoryConsultAddDropRequest(BaseModel):
    """
    Add/drop consult payload. Two shapes are accepted:

      * Guided  — at least one of ``add_course_ids`` /
        ``drop_course_ids`` is non-empty. The agent evaluates that
        specific hypothetical change.
      * Proactive — both lists empty (or body omitted entirely).
        The agent reviews the active registration against the
        curriculum + history and proactively recommends adds/drops
        (or confirms the plan is healthy).

    Everything else (the student's REGISTERED registration for the
    open term, currently enrolled courses, completed courses, CGPA,
    department curriculum) is server-resolved.
    """
    add_course_ids: list[uuid.UUID] = Field(default_factory=list)
    drop_course_ids: list[uuid.UUID] = Field(default_factory=list)


class GraduationImpactRead(BaseModel):
    """Forward-looking graduation-trajectory snapshot."""
    semesters_remaining: Optional[int] = None
    on_track: Optional[bool] = None
    expected_graduation_semester: Optional[int] = None
    delay_semesters: Optional[int] = None
    critical_path_courses: list[str] = Field(default_factory=list)


class ConsultationRecommendedCourse(BaseModel):
    """A single LLM-recommended course (post-validation)."""
    course_code: str
    title: str
    credit_hours: int
    is_core: bool
    reason: str
    requires_override: Optional[bool] = None


class AdvisoryConsultResponse(BaseModel):
    """
    Response shape for all three /advisory/consult/* endpoints.
    Mirrors :class:`ConsultationResult` but uses the persisted row's
    UUID + timestamps so the student can refer back to this exact
    consultation later.
    """
    model_config = ConfigDict(from_attributes=False)

    recommendation_id: uuid.UUID
    student_id: uuid.UUID
    term_id: uuid.UUID
    mode: ConsultationMode
    verdict: str
    risk_status: RiskStatus
    narrative: str
    recommended_courses: list[ConsultationRecommendedCourse] = Field(
        default_factory=list
    )
    warnings: list[str] = Field(default_factory=list)
    graduation_impact: GraduationImpactRead = Field(
        default_factory=GraduationImpactRead
    )
    filtered_recommendations: list[str] = Field(default_factory=list)
    created_at: datetime


# ══════════════════════════════════════════════════════════════
#  Bridge: admission Enrollment → course-management Student
# ══════════════════════════════════════════════════════════════


class StudentOnboardRequest(BaseModel):
    """Officer payload for onboarding a Student from an Enrollment row."""
    enrollment_id: uuid.UUID


class StudentResponse(BaseModel):
    """Read view of a Student row."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    student_id: str
    full_name: str
    current_semester: int
    enrollment_status: EnrollmentStatus


class DashboardSection(BaseModel):
    """Cohort section the student is allocated to in the current term.
    Rooms are per-slot now and surface through the schedule endpoints."""
    section_id: uuid.UUID
    section_code: str
    capacity: int
    enrolled_count: int


class DashboardCurrentTerm(BaseModel):
    """The currently-open AcademicTerm + the student's status in it."""
    term_id: uuid.UUID
    term_name: str
    start_date: date
    end_date: date
    registration_status: Optional[RegistrationStatus] = None
    section: Optional[DashboardSection] = None


class StudentDashboardResponse(BaseModel):
    """
    Consolidated payload for ``GET /api/v1/courses/me`` — everything
    a student needs on their portal home: identity (name, email, UGR
    id), academic context (department, semester, sponsorship,
    enrollment status), and the current-term context (term name +
    cohort section). ``current_term`` is null if no term is open.
    """
    student_id: str
    full_name: str
    email: Optional[str] = None
    department: Optional[str] = None
    current_semester: int
    sponsorship_type: Optional[SponsorshipType] = None
    enrollment_status: EnrollmentStatus
    current_term: Optional[DashboardCurrentTerm] = None


# ══════════════════════════════════════════════════════════════
#  Department-Head prerequisite override (SRS §3.5)
# ══════════════════════════════════════════════════════════════


class PrerequisiteOverrideRequest(BaseModel):
    """
    Department Head's payload to bypass the prereq check for a
    single (registration, course) pair.
    """
    course_id: uuid.UUID
    justification: str = Field(..., min_length=3, max_length=4000)


class PrerequisiteOverrideResponse(BaseModel):
    """Read view of a PrerequisiteOverride row."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    registration_id: uuid.UUID
    course_id: uuid.UUID
    granted_by_id: uuid.UUID
    justification: str


# ══════════════════════════════════════════════════════════════
#  Instructor management (Department-Head endpoints)
# ══════════════════════════════════════════════════════════════


class InstructorCreateRequest(BaseModel):
    """
    Department-Head payload for ``POST /officer/instructors``. The
    portal generates a 4-digit PIN, hashes it onto a fresh User row,
    and emails the staff_id + PIN to the instructor — same lifecycle
    as student onboarding.
    """
    staff_id: str = Field(..., min_length=3, max_length=20,
                          examples=["STAFF/0042/14"])
    email: str = Field(..., min_length=3, max_length=255)
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    department: str = Field(..., min_length=1, max_length=100)


class InstructorResponse(BaseModel):
    """Read view of an Instructor row."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    instructor_id: str
    department: str


class InstructorAssignmentCreate(BaseModel):
    """
    Department-Head payload for ``POST /officer/instructor-assignments``.
    Reposting with a different instructor for the same (course, term)
    silently rebinds — the service guarantees one active assignment
    per (course, term).
    """
    instructor_id: uuid.UUID
    course_id: uuid.UUID
    term_id: uuid.UUID


class InstructorAssignmentResponse(BaseModel):
    """Read view of an InstructorAssignment row."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    instructor_id: uuid.UUID
    course_id: uuid.UUID
    term_id: uuid.UUID
