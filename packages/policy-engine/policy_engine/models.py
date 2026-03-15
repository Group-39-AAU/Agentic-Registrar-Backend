from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class PolicyVersion(BaseModel):
    """Represents a concrete version of a policy."""

    name: str = Field(..., description="Human-readable policy name.")
    version: str = Field(..., description="Semantic version, e.g. '1.0.0'.")
    effective_from: date | None = Field(
        default=None,
        description="Date from which this policy version is effective.",
    )
    effective_to: date | None = Field(
        default=None,
        description="Date after which this policy version is no longer effective.",
    )
    description: str | None = Field(default=None)

