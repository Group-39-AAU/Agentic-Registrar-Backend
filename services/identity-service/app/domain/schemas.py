from __future__ import annotations

from typing import List

from pydantic import BaseModel, EmailStr, Field


class RoleOut(BaseModel):
    id: str
    name: str


class UserOut(BaseModel):
    id: str
    username: str
    email: EmailStr
    is_active: bool
    roles: List[RoleOut] = Field(default_factory=list)


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenIntrospectRequest(BaseModel):
    token: str


class TokenIntrospectResponse(BaseModel):
    active: bool
    sub: str | None = None
    roles: list[str] = Field(default_factory=list)

