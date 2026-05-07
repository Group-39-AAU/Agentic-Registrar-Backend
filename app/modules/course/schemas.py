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
    AddDropAction, AddDropRequestStatus, EnrollmentStatus, RegistrationStatus,
    RiskStatus, SponsorshipType,
)


# ══════════════════════════════════════════════════════════════
#  AcademicTerm — officer window control
# ══════════════════════════════════════════════════════════════


class AcademicTermResponse(BaseModel):
    """Read view of an AcademicTerm."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    term_name: str
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


# ══════════════════════════════════════════════════════════════
#  Registration
# ══════════════════════════════════════════════════════════════


class RegistrationDraftCreate(BaseModel):
    """
    Request: student creates a draft registration for a term.

    The endpoint resolves the calling student from get_current_user,
    so student_id is not in the payload. ``sponsorship_type`` is
    inherited from the student's admission record (denormalised onto
    Student.sponsorship_type at onboarding time) — the student does
    not pick it per registration.
    """
    term_id: uuid.UUID


class RegistrationCourseAdd(BaseModel):
    """Request: add a course to an existing draft."""
    course_id: uuid.UUID


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
    Officer payload for ``POST /officer/schedule/generate``. Department
    is no longer a parameter — every (department, semester) cohort in
    the term is processed in one call.
    """
    term_id: uuid.UUID


class SectionRead(BaseModel):
    """Cohort section header (without slots)."""
    section_id: uuid.UUID
    section_code: str
    department: str
    semester: int
    room: Optional[str] = None
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
    """One row in an instructor's per-term schedule view."""
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


class ScheduleGenerateResponse(BaseModel):
    """Payload returned from the officer's generate endpoint."""
    allocation: dict
    schedule: dict


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
    requires_officer_review: bool
    reviewed_by_id: Optional[uuid.UUID] = None
    reviewed_at: Optional[datetime] = None
    review_notes: Optional[str] = None


class AdvisoryReviewCloseRequest(BaseModel):
    """Officer payload for closing an advisory HITL review."""
    review_notes: str = Field(..., min_length=3, max_length=4000)


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
    """Cohort section the student is allocated to in the current term."""
    section_id: uuid.UUID
    section_code: str
    room: Optional[str] = None
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
