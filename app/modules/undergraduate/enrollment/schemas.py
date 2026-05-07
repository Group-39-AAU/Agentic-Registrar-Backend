"""
Enrollment module — Pydantic schemas.
"""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class EnrollmentResponse(BaseModel):
    """Response: enrollment details for one student."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    application_id: uuid.UUID
    applicant_id: uuid.UUID
    university_id: str
    program_id: Optional[uuid.UUID] = None
    department: str
    enrollment_term: str
    created_at: datetime


class EnrollmentRunResponse(BaseModel):
    """Response after running enrollment batch."""
    enrolled_count: int
    skipped_count: int
    message: str
    enrollments: list[EnrollmentResponse]


class EnrollmentListResponse(BaseModel):
    """Paginated list of enrollments."""
    items: list[EnrollmentResponse]
    total: int
    page: int
    page_size: int
