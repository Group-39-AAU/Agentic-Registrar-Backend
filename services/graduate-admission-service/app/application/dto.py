from __future__ import annotations

from pydantic import BaseModel, EmailStr

from ..domain.enums import ApplicationStatus


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
    gat_score: float | None = None
    transcript_score: float | None = None
    cumulative_score: float | None = None
    ranking_position: int | None = None


class ApplicationListResponse(BaseModel):
    items: list[ApplicationResponse]


class ApplicationDocumentCreateRequest(BaseModel):
    document_id: str
    document_type: str


class DepartmentDecisionRequest(BaseModel):
    approved: bool
    comments: str | None = None


class RegistrarDecisionRequest(BaseModel):
    approved: bool
    comments: str | None = None

