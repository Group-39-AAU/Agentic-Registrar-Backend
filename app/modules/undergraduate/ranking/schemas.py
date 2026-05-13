"""
Ranking module — Pydantic schemas.
"""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.shared.enums import StreamType


# ── Ranking Result ──

class RankingResultResponse(BaseModel):
    """One applicant's ranking result."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    admission_term_id: uuid.UUID
    ranking_run_number: int
    application_id: uuid.UUID
    applicant_full_name: Optional[str] = None
    grade12_score: float
    uat_score: float
    final_score: float
    category: str
    rank_position: int
    assigned_program_id: Optional[uuid.UUID] = None
    assigned_program_department: Optional[str] = None
    assigned_stream: Optional[StreamType] = None
    is_assigned: bool
    assignment_detail: Optional[str] = None
    created_at: datetime


# ── Ranking Run Response ──

class RankingRunResponse(BaseModel):
    """Response after triggering a ranking batch."""
    term_id: uuid.UUID
    run_number: int
    total_processed: int
    self_sponsored_count: int
    government_count: int
    assigned_count: int
    unassigned_count: int
    message: str


# ── Cutoff Info ──

class ProgramCutoffResponse(BaseModel):
    """Cutoff score for a specific program after ranking."""
    program_id: uuid.UUID
    program_code: str
    program_name: str
    stream: StreamType
    max_capacity: Optional[int]
    assigned_count: int
    cutoff_score: Optional[float]


class StreamCutoffResponse(BaseModel):
    """Cutoff score for a stream after ranking."""
    stream: StreamType
    max_capacity: int
    assigned_count: int
    cutoff_score: Optional[float]


class RankingSummaryResponse(BaseModel):
    """Overall ranking batch summary with cutoffs."""
    term_id: uuid.UUID
    program_cutoffs: list[ProgramCutoffResponse]
    stream_cutoffs: list[StreamCutoffResponse]
    total_assigned: int
    total_unassigned: int


# ── Stream Quota ──

class StreamQuotaResponse(BaseModel):
    """Response for stream quota."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    stream: StreamType
    max_capacity: int
    admission_term_id: uuid.UUID

class StreamQuotaUpdate(BaseModel):
    """Request to update stream quota."""
    max_capacity: int


class StreamQuotaCreate(BaseModel):
    """Request to create a stream quota for a specific admission term."""
    stream: StreamType
    max_capacity: int
    admission_term_id: uuid.UUID


# ── Officer Review ──

class StudentReviewCard(BaseModel):
    """Everything an officer needs to make an admission decision."""
    application_id: uuid.UUID
    student_name: str
    admission_number: str
    sponsorship_type: str
    stream: str

    # Scores
    grade12_score: float
    uat_score: float
    final_score: float
    rank_position: int

    # Placement
    assigned_program_name: Optional[str] = None
    assigned_program_code: Optional[str] = None
    assigned_stream: Optional[str] = None
    is_assigned: bool
    assignment_detail: Optional[str] = None

    # Preferences (self-sponsored)
    program_choice_1: Optional[str] = None
    program_choice_2: Optional[str] = None
    program_choice_3: Optional[str] = None

    # AI recommendation
    ai_recommended_decision: Optional[str] = None
    ai_confidence: Optional[float] = None

    # Status
    current_status: str
    has_decision: bool


class StudentReviewListResponse(BaseModel):
    """Paginated list of students for officer review."""
    items: list[StudentReviewCard]
    total: int
    page: int
    page_size: int


# ── Batch Decision ──

class BatchDecisionItem(BaseModel):
    """One decision in a batch."""
    application_id: uuid.UUID
    human_decision: str  # ADMIT or REJECT
    justification_remarks: str


class BatchDecisionRequest(BaseModel):
    """Request: officer submits multiple decisions at once."""
    decisions: list[BatchDecisionItem]


class BatchDecisionResponse(BaseModel):
    """Response after processing a batch of decisions."""
    processed: int
    failed: int
    results: list[dict]

