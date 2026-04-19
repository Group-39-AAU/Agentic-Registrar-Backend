"""
Auth module — Pydantic schemas for registration, login, and user responses.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.shared.enums import UserRole


class RegisterRequest(BaseModel):
    """Request: create a new student account."""
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)


class LoginRequest(BaseModel):
    """Request: authenticate with email and password."""
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    """Response: JWT access token after successful login."""
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    """Response: public user profile."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    first_name: str
    last_name: str
    role: UserRole
    is_active: bool
    created_at: datetime
