"""
MoE module — Pydantic schemas.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.shared.enums import StreamType


class MoeRecordResponse(BaseModel):
    """Response: a single MoE student record."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    admission_number: str
    full_name: str
    exam_year: int
    stream: StreamType
    subjects: dict
    total_score: float
    created_at: datetime
