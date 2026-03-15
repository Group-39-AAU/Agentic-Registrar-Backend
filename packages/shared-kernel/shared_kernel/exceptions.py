from __future__ import annotations

from typing import Any, Mapping


class AppError(Exception):
    """Base application error with optional machine-readable code and details."""

    def __init__(self, message: str, *, code: str | None = None, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = dict(details) if details is not None else {}

    @property
    def message(self) -> str:
        return str(self)


class NotFoundError(AppError):
    """Raised when a requested resource is not found."""


class ValidationError(AppError):
    """Raised when validation of input or state fails."""


class ConflictError(AppError):
    """Raised when an operation conflicts with existing state."""


class UnauthorizedError(AppError):
    """Raised when authentication fails or is missing."""


class ForbiddenError(AppError):
    """Raised when a principal lacks permission for an operation."""


class DependencyError(AppError):
    """Raised when a downstream dependency fails (e.g. database, cache, external API)."""

