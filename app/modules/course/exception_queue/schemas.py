"""
Unified exception queue schemas.

One normalised shape per pending exception. ``source`` is the
discriminator the UI uses to choose which resolution flow to send
the officer to.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class ExceptionSource(str, Enum):
    """
    Discriminator for the three sources joined into the unified
    queue. Values match the prefix the deep_link is rooted under so
    routing on the client is mechanical.
    """
    ADVISORY = "ADVISORY"   # Track A HIGH-risk advisory verdicts
    GRADING = "GRADING"     # Track B FLAGGED grade batches
    STANDING = "STANDING"   # Track C held-for-review standings


class ExceptionQueueEntry(BaseModel):
    """
    One normalised row of the queue. Carries enough context for the
    officer to triage from the list page — student identity, a
    one-line summary, the source-specific id, and a deep_link the
    UI uses to navigate to the per-source resolution endpoint.
    """
    source: ExceptionSource
    source_id: uuid.UUID
    student_id: Optional[uuid.UUID] = None
    student_number: Optional[str] = None
    full_name: Optional[str] = None
    term_id: Optional[uuid.UUID] = None
    term_name: Optional[str] = None
    department: Optional[str] = None
    summary: str
    deep_link: str
    created_at: datetime
