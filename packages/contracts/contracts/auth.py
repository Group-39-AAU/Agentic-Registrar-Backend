from __future__ import annotations

from datetime import datetime
from typing import Sequence

from pydantic import BaseModel, Field

from .enums import AuthTokenType


class AuthTokenPayload(BaseModel):
    """Common JWT payload fields shared across services."""

    sub: str = Field(..., description="Subject identifier (e.g. user id).")
    iat: datetime = Field(..., description="Issued-at timestamp.")
    exp: datetime = Field(..., description="Expiry timestamp.")
    type: AuthTokenType = Field(default=AuthTokenType.ACCESS)
    scope: Sequence[str] = Field(default_factory=list, description="OAuth-style scopes.")
    roles: Sequence[str] = Field(default_factory=list, description="Application roles.")

