from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, Field

from agent_core.state import GraphContext, GraphState


class CourseWorkflowData(BaseModel):
    registration_id: str
    student_id: str
    term: str
    validated: bool | None = None
    finalized: bool | None = None
    grades_authorized: bool | None = None
    standing_authorized: bool | None = None


class CourseWorkflowState(GraphState[Dict[str, Any]]):
    data: Dict[str, Any] = Field(default_factory=dict)
    context: GraphContext = Field(default_factory=GraphContext)
    history: List[Dict[str, Any]] = Field(default_factory=list)

