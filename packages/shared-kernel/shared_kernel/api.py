from __future__ import annotations

from http import HTTPStatus
from typing import Any

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    code: str = Field(..., description="Machine-readable error code.")
    message: str = Field(..., description="Human-readable error message.")
    target: str | None = Field(None, description="Optional field or entity the error relates to.")


class ErrorResponse(BaseModel):
    status: int = Field(..., description="HTTP status code.")
    error: ErrorDetail


class HealthStatus(BaseModel):
    status: str = Field(..., description="Overall health status, e.g. 'ok' or 'degraded'.")
    details: dict[str, Any] = Field(default_factory=dict)


def make_error_response(
    status: HTTPStatus,
    code: str,
    message: str,
    *,
    target: str | None = None,
) -> ErrorResponse:
    """Helper for building consistent error responses."""

    return ErrorResponse(
        status=status.value,
        error=ErrorDetail(code=code, message=message, target=target),
    )

