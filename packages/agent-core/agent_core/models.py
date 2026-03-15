from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class GraphExecutionMetadata(BaseModel):
    """Metadata captured for each workflow execution or run."""

    run_id: str = Field(..., description="Unique identifier for this graph run.")
    graph_name: str = Field(..., description="Logical name of the graph.")
    started_at: datetime = Field(..., description="When the run started.")
    finished_at: datetime | None = Field(
        default=None,
        description="When the run finished, if completed.",
    )
    status: str = Field(..., description="Execution status, e.g. 'running', 'completed', 'failed'.")
    metadata: dict[str, Any] = Field(default_factory=dict)


class HumanInTheLoopCheckpoint(BaseModel):
    """Represents a pause in execution awaiting human input."""

    checkpoint_id: str
    run_id: str
    created_at: datetime
    reason: str
    payload: dict[str, Any] = Field(default_factory=dict)


class GuardrailResult(BaseModel):
    """Result of applying guardrails to a step output."""

    is_valid: bool
    reasons: list[str] = Field(default_factory=list)
    corrected_output: Any | None = Field(
        default=None,
        description="Optional corrected output proposed by the guardrail.",
    )

