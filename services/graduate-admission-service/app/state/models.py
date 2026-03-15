from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, Field

from agent_core.state import GraphContext, GraphState

from ..domain.enums import ApplicationStatus


class GraduateWorkflowData(BaseModel):
    application_id: str
    status: ApplicationStatus
    payment_verified: bool | None = None
    gat_score: float | None = None
    transcript_verified: bool | None = None
    department_score: float | None = None
    capacity_ok: bool | None = None
    department_approved: bool | None = None
    registrar_approved: bool | None = None


class GraduateWorkflowState(GraphState[Dict[str, Any]]):
    data: Dict[str, Any] = Field(default_factory=dict)
    context: GraphContext = Field(default_factory=GraphContext)
    history: List[Dict[str, Any]] = Field(default_factory=list)

