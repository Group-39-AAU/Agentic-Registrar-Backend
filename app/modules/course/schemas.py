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
    AddDropAction, AddDropRequestStatus, RegistrationStatus, SponsorshipType,
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
    so student_id is not in the payload.
    """
    term_id: uuid.UUID
    sponsorship_type: SponsorshipType


class RegistrationCourseAdd(BaseModel):
    """Request: add a course to an existing draft."""
    course_id: uuid.UUID


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
    """Officer payload for ``POST /officer/schedule/generate``."""
    term_id: uuid.UUID
    department: str = Field(..., min_length=1, max_length=100)


class SectionTimetableEntry(BaseModel):
    """One row in a student's or instructor's timetable view."""
    section_id: uuid.UUID
    course_code: str
    course_title: str
    section_code: str
    room: Optional[str] = None
    time_slot: Optional[str] = None
    instructor_id: Optional[uuid.UUID] = None


class TimetableResponse(BaseModel):
    """Read-only timetable for a single actor."""
    term_id: uuid.UUID
    entries: list[SectionTimetableEntry] = Field(default_factory=list)


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
    target_section_id: Optional[uuid.UUID] = None


class AddDropRequestResponse(BaseModel):
    """Read view of an AddDropRequest."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    registration_id: uuid.UUID
    course_id: uuid.UUID
    target_section_id: Optional[uuid.UUID] = None
    action: AddDropAction
    deadline_snapshot: date
    status: AddDropRequestStatus
    reason: Optional[str] = None
    override_by_id: Optional[uuid.UUID] = None
    override_justification: Optional[str] = None


class AddDropOverrideRequest(BaseModel):
    """Officer override payload."""
    justification: str = Field(..., min_length=3, max_length=4000)
