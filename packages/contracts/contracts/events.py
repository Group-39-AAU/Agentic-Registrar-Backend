from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class EventEnvelope(BaseModel):
    """Generic event envelope for internal and external messaging."""

    id: str = Field(..., description="Unique identifier for the event.")
    type: str = Field(..., description="Event type identifier, e.g. 'identity.user.created'.")
    source: str = Field(..., description="Originating service or component.")
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(..., description="Time at which the event occurred.")
    correlation_id: str | None = Field(
        default=None,
        description="Optional correlation or trace identifier.",
    )

