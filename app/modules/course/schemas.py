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

from app.shared.enums import RegistrationStatus, SponsorshipType


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
