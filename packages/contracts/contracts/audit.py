from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .enums import AuditAction


class AuditLogEntry(BaseModel):
    id: str = Field(..., description="Unique identifier for this audit record.")
    actor_id: str = Field(..., description="Identifier of the actor performing the action.")
    action: AuditAction
    resource_type: str = Field(..., description="Type or category of the resource touched.")
    resource_id: str = Field(..., description="Identifier of the resource touched.")
    occurred_at: datetime = Field(..., description="Timestamp of when the action occurred.")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional, action-specific contextual metadata.",
    )

