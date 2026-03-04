"""
Undergraduate Admission module — Pydantic schemas.

Schemas are grouped by entity and by direction (Create / Update / Response).
"""

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.shared.enums import (
    ApplicationStatus, DecisionType, DocumentType, VerificationStatus,
)


# ══════════════════════════════════════════════════════════════
#  Application Schemas
# ══════════════════════════════════════════════════════════════


class ApplicationCreate(BaseModel):
    """Request: student submits a new application."""
    program_id: uuid.UUID
    admission_term: str = Field(..., min_length=1, max_length=50, examples=["Fall 2026"])
    extra_data: dict[str, Any] = Field(default_factory=dict)


class ApplicationStatusUpdate(BaseModel):
    """Request: registrar/system transitions application status."""
    new_status: ApplicationStatus
    trigger_reason: Optional[str] = None


class ApplicationResponse(BaseModel):
    """Response: full application details."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    applicant_id: uuid.UUID
    program_id: uuid.UUID
    admission_term: str
    current_status: ApplicationStatus
    final_decision: Optional[str] = None
    remarks: Optional[str] = None
    extra_data: dict[str, Any]
    is_deleted: bool
    created_at: datetime
    updated_at: datetime


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
