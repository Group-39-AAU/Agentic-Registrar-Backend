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
    """
    Request: authenticate with either an email (admission applicants)
    or a UGR student ID (post-enrollment portal users).
    """
    identifier: str = Field(
        ..., min_length=3, max_length=255,
        description="Email (e.g. abel@aau.edu.et) or UGR ID (e.g. UGR/0001/14)",
    )
    password: str


class TokenResponse(BaseModel):
    """Response: JWT access token after successful login."""
    access_token: str
    token_type: str = "bearer"
    # True when the student was just onboarded with a temp PIN —
    # client must redirect to the change-password screen and call
    # POST /auth/change-password before any other endpoint will work.
    must_change_password: bool = False


class ChangePasswordRequest(BaseModel):
    """
    Request: student replaces their temporary PIN (or any current
    password) with a permanent one. The new password follows the
    same policy as registration.
    """
    current_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=8, max_length=128)


class ForgotPasswordRequest(BaseModel):
    """
    Request: ask for a password-reset email. The endpoint always
    responds 204 — we never reveal whether the email exists in the
    database, so an attacker can't enumerate accounts.
    """
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """
    Request: complete a password reset using the short-lived token
    that was emailed to the user.
    """
    token: str = Field(..., min_length=10, max_length=4096)
    new_password: str = Field(..., min_length=8, max_length=128)


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
