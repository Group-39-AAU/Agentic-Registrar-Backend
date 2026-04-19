"""
Testing Center module — Pydantic schemas.
"""

import uuid
from typing import Optional

from pydantic import BaseModel


class UATRecordResponse(BaseModel):
    """Response schema for a UAT record."""
    id: uuid.UUID
    uat_id: str
    application_id: uuid.UUID
    student_name: str
    score: Optional[float] = None
    is_completed: bool

    model_config = {"from_attributes": True}


class UATCallbackResponse(BaseModel):
    """Response after UAT score is received."""
    uat_id: str
    score: float
    message: str
