from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from agent_core.state import GraphContext, GraphState
from ..domain.enums import ApplicationStatus


class UndergradWorkflowData(BaseModel):
    application_id: str
    status: ApplicationStatus
    is_payment_valid: bool | None = None
    has_extraction_issue: bool | None = None
    moe_verified: bool | None = None
    uat_score: float | None = None
    cgpa: float | None = None
    cumulative_score: float | None = None
    ranked_position: int | None = None
    flagged_for_review: bool = False
    approved: bool | None = None


class UndergradWorkflowState(GraphState[Dict[str, Any]]):
    data: Dict[str, Any] = Field(default_factory=dict)
    context: GraphContext = Field(default_factory=GraphContext)
    history: List[Dict[str, Any]] = Field(default_factory=list)

