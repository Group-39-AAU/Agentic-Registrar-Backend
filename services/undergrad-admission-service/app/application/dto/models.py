from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field

from ...domain.enums import ApplicationStatus


class ApplicantCreateRequest(BaseModel):
    full_name: str
    email: EmailStr
    phone_number: str | None = None
    national_id: str | None = None


class ApplicantResponse(BaseModel):
    id: str
    full_name: str
    email: EmailStr
    phone_number: str | None = None
    national_id: str | None = None


class ApplicationCreateRequest(BaseModel):
    applicant_id: str
    program_code: str
    intake_year: int


class ApplicationResponse(BaseModel):
    id: str
    applicant_id: str
    program_code: str
    intake_year: int
    status: ApplicationStatus
    cumulative_score: float | None = None
    ranking_position: int | None = None


class ApplicationListResponse(BaseModel):
    items: List[ApplicationResponse]


class ApplicationDocumentCreateRequest(BaseModel):
    document_id: str
    document_type: str


class OfficerDecisionRequest(BaseModel):
    decision: ApplicationStatus = Field(
        ...,
        description="Final decision status, e.g. APPROVED or REJECTED.",
    )
    reason: str | None = None

