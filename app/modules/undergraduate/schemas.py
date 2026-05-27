"""
Undergraduate Admission module — Pydantic schemas.

Schemas are grouped by entity and by direction (Create / Update / Response).
"""

import uuid
from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.shared.enums import (
    ApplicationStatus, DecisionType, DocumentType,
    PaymentStatus, SponsorshipType, StreamType, VerificationStatus,
)


# ══════════════════════════════════════════════════════════════
#  Application Schemas
# ══════════════════════════════════════════════════════════════


class ApplicationCreate(BaseModel):
    """
    Request: student submits a new application.

    Business rules:
    - All students must select a sponsorship_type and stream.
    - Self-sponsored: must provide exactly 3 program choices.
    - Government-sponsored: program choices must be null.
    """
    sponsorship_type: SponsorshipType
    stream: StreamType
    admission_number: str = Field(..., min_length=1, max_length=50, examples=["2955397"])
    admission_term_id: uuid.UUID

    # Self-sponsored only
    program_choice_1_id: Optional[uuid.UUID] = None
    program_choice_2_id: Optional[uuid.UUID] = None
    program_choice_3_id: Optional[uuid.UUID] = None

    extra_data: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_program_choices(self):
        choices = [self.program_choice_1_id, self.program_choice_2_id, self.program_choice_3_id]
        if self.sponsorship_type == SponsorshipType.SELF_SPONSORED:
            if any(c is None for c in choices):
                raise ValueError(
                    "Self-sponsored students must provide exactly 3 program choices"
                )
            if len(set(choices)) != 3:
                raise ValueError("All 3 program choices must be different")
        else:
            # Government-sponsored: no program choices
            if any(c is not None for c in choices):
                raise ValueError(
                    "Government-sponsored students should not provide program choices"
                )
        return self


class ApplicationStatusUpdate(BaseModel):
    """Request: registrar/system transitions application status."""
    new_status: ApplicationStatus
    trigger_reason: Optional[str] = None


class CorrectionUpdateRequest(BaseModel):
    """Request: student submits identity/admission corrections."""

    admission_number: Optional[str] = Field(default=None, min_length=1, max_length=50)
    first_name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    last_name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    stream: Optional[StreamType] = None

    @model_validator(mode="after")
    def validate_at_least_one_field(self):
        if not any([self.admission_number, self.first_name, self.last_name, self.stream]):
            raise ValueError("Provide at least one field to update")
        return self


class FlagResolutionRequest(BaseModel):
    """Request: admin resolves a flagged application."""

    action: str = Field(
        ...,
        description=(
            "One of: APPROVE_AND_CONTINUE, REQUEST_STUDENT_CORRECTION, "
            "ESCALATE_TO_PENDING_REVIEW, REJECT_NOW"
        ),
    )
    resolution_note: str = Field(..., min_length=1, max_length=500)


class FlagContextResponse(BaseModel):
    """Response: latest AI reasoning details for a flagged application."""

    application_id: uuid.UUID
    current_status: ApplicationStatus
    latest_ai_recommendation: Optional[DecisionType] = None
    latest_ai_confidence: Optional[float] = None
    latest_ai_summary: Optional[str] = None
    traces: list[dict[str, str]] = Field(default_factory=list)


class CorrectionReasoningStep(BaseModel):
    """One human-readable step from the agent's reasoning trace."""
    label: str
    detail: str


class CorrectionContextResponse(BaseModel):
    """
    Student-facing reasoning bundle for an application sitting in
    CHANGES_REQUESTED. Contains only natural-language fields — no
    internal step names, decision enums, or confidence scores — so
    the student can read it directly and understand what to fix.
    """

    officer_note: Optional[str] = None
    agent_summary: Optional[str] = None
    reasoning_steps: list[CorrectionReasoningStep] = Field(default_factory=list)


class ReRunChecksResponse(BaseModel):
    """Response: status after re-running post-payment checks."""

    application: "ApplicationResponse"
    message: str


class ProgramChoiceSummary(BaseModel):
    """Program fields exposed on application responses (from academic_programs)."""
    id: uuid.UUID
    code: str
    name: str


class ApplicationResponse(BaseModel):
    class AdmissionTermSummary(BaseModel):
        id: uuid.UUID
        term_name: str

    """
    Response: full application details for all undergraduate application endpoints.

    Program choices are objects (not raw FKs). For self-sponsored applicants they are
    populated from academic_programs; for government-sponsored they are null.
    uat_id comes from uat_records when a row exists.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    applicant_id: uuid.UUID
    applicant_email: Optional[str] = None
    applicant_first_name: Optional[str] = None
    applicant_last_name: Optional[str] = None
    sponsorship_type: SponsorshipType
    stream: StreamType
    admission_number: str
    program_choice_1: Optional[ProgramChoiceSummary] = None
    program_choice_2: Optional[ProgramChoiceSummary] = None
    program_choice_3: Optional[ProgramChoiceSummary] = None
    admission_term: AdmissionTermSummary
    current_status: ApplicationStatus
    final_decision: Optional[str] = None
    payment_status: PaymentStatus
    payment_reference: Optional[str] = None
    remarks: Optional[str] = None
    extra_data: dict[str, Any]
    is_deleted: bool
    created_at: datetime
    updated_at: datetime
    uat_id: Optional[str] = None


class ApplicationListResponse(BaseModel):
    """Response: paginated list of applications."""
    items: list[ApplicationResponse]
    total: int


class ApplicationExistsResponse(BaseModel):
    """Response: whether the applicant already has an application for a term."""
    admission_term_id: uuid.UUID
    has_existing_application: bool


class AdmissionTermCreate(BaseModel):
    term_name: str = Field(..., min_length=1, max_length=100)
    start_date: date
    end_date: date
    is_open: bool = True
    description: Optional[str] = None


class AdmissionTermResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    term_name: str
    start_date: date
    end_date: date
    is_open: bool
    description: Optional[str] = None


# ══════════════════════════════════════════════════════════════
#  Document Schemas
# ══════════════════════════════════════════════════════════════


class DocumentCreate(BaseModel):
    """Request: attach a document to an application."""
    application_id: uuid.UUID
    document_type: DocumentType
    storage_path: str = Field(..., min_length=1, max_length=512)


class DocumentVerify(BaseModel):
    """Request: officer verifies/rejects a document."""
    verification_status: VerificationStatus


class DocumentResponse(BaseModel):
    """Response: full document details."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    application_id: uuid.UUID
    document_type: DocumentType
    storage_path: str
    verification_status: VerificationStatus
    verified_by_id: Optional[uuid.UUID] = None
    is_deleted: bool
    created_at: datetime
    updated_at: datetime


# ══════════════════════════════════════════════════════════════
#  Status History Schemas (read-only)
# ══════════════════════════════════════════════════════════════


class StatusHistoryResponse(BaseModel):
    """Response: one entry in the application status history ledger."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    application_id: uuid.UUID
    previous_status: Optional[ApplicationStatus] = None
    new_status: ApplicationStatus
    changed_by_id: Optional[uuid.UUID] = None
    trigger_reason: Optional[str] = None
    created_at: datetime


# ══════════════════════════════════════════════════════════════
#  Registrar Decision Schemas
# ══════════════════════════════════════════════════════════════


class DecisionCreate(BaseModel):
    """Request: registrar officer records a final decision."""
    human_decision: DecisionType
    justification_remarks: str = Field(..., min_length=1)
    override_reason: Optional[str] = None


class DecisionResponse(BaseModel):
    """Response: registrar decision details."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    application_id: uuid.UUID
    reviewer_id: uuid.UUID
    human_decision: DecisionType
    justification_remarks: str
    override_reason: Optional[str] = None
    created_at: datetime


# ══════════════════════════════════════════════════════════════
#  Payment Schemas
# ══════════════════════════════════════════════════════════════


class PaymentInitiateResponse(BaseModel):
    """Response: simulated payment link after initiation."""
    application_id: uuid.UUID
    payment_reference: str
    payment_url: str
    message: str = "Simulated payment link generated"


class PaymentCallbackRequest(BaseModel):
    """Request: simulated payment gateway callback."""
    payment_reference: str
    status: PaymentStatus = PaymentStatus.COMPLETED


# ══════════════════════════════════════════════════════════════
#  AI Evaluation Schemas (read-only for now)
# ══════════════════════════════════════════════════════════════


class AIEvaluationResponse(BaseModel):
    """Response: AI agent recommendation for an application."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    application_id: uuid.UUID
    agent_version: str
    recommended_decision: DecisionType
    confidence_score: float
    is_overridden: bool
    summary_reasoning: Optional[str] = None
    created_at: datetime
