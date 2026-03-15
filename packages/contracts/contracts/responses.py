from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ResponseMeta(BaseModel):
    request_id: str | None = Field(
        default=None,
        description="Correlation or request identifier, if available.",
    )
    success: bool = Field(default=True)
    message: str | None = Field(default=None, description="Optional human-readable message.")


class ResponseEnvelope(Generic[T], BaseModel):
    """Generic response envelope used for all public APIs."""

    meta: ResponseMeta = Field(default_factory=ResponseMeta)
    data: T | None = Field(default=None)
    error: dict[str, Any] | None = Field(default=None)

