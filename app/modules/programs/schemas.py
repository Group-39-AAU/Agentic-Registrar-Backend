"""
Programs module — Pydantic schemas.
"""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.shared.enums import StreamType


class ProgramResponse(BaseModel):
    """Response: public program details for students to browse."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    department: str
    stream: StreamType
    cut_off_score: Optional[float] = None
    max_capacity: Optional[int] = None
    is_active: bool
    created_at: datetime


class ProgramListResponse(BaseModel):
    """Response: list of programs."""
    items: list[ProgramResponse]
    total: int
