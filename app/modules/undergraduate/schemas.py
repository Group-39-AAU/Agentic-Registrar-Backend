"""
Undergraduate Admission module — Pydantic schemas.

Schemas are grouped by entity and by direction (Create / Update / Response).
"""

import uuid
from datetime import datetime
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
    admission_term: str = Field(..., min_length=1, max_length=50, examples=["Fall 2026"])

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


class ProgramChoiceSummary(BaseModel):
    """Program fields exposed on application responses (from academic_programs)."""
    id: uuid.UUID
    code: str
    name: str


class ApplicationResponse(BaseModel):
    """
    Response: full application details for all undergraduate application endpoints.

    Program choices are objects (not raw FKs). For self-sponsored applicants they are
    populated from academic_programs; for government-sponsored they are null.
    uat_id comes from uat_records when a row exists.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    applicant_id: uuid.UUID
    sponsorship_type: SponsorshipType
    stream: StreamType
    admission_number: str
    program_choice_1: Optional[ProgramChoiceSummary] = None
    program_choice_2: Optional[ProgramChoiceSummary] = None
    program_choice_3: Optional[ProgramChoiceSummary] = None
    admission_term: str
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
