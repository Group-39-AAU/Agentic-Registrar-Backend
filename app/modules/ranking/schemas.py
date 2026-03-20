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
    ranking_batch_id: str
    application_id: uuid.UUID
    grade12_score: float
    uat_score: float
    final_score: float
    category: str
    rank_position: int
    assigned_program_id: Optional[uuid.UUID] = None
    assigned_stream: Optional[StreamType] = None
    is_assigned: bool
    assignment_detail: Optional[str] = None
    created_at: datetime


# ── Ranking Run Response ──

class RankingRunResponse(BaseModel):
    """Response after triggering a ranking batch."""
    batch_id: str
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
    batch_id: str
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
    admission_term: str

class StreamQuotaUpdate(BaseModel):
    """Request to update stream quota."""
    max_capacity: int
