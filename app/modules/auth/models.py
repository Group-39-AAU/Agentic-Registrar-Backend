"""
Auth module — User model.
"""

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import SoftDeleteBase
from app.shared.enums import UserRole


class User(SoftDeleteBase):
    """
    Represents applicants, registrar officers, and admins.
    Uses SoftDeleteBase (core domain entity — never hard-deleted).
    """
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[UserRole] = mapped_column(nullable=False, default=UserRole.STUDENT)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Set True when an officer onboards the student into the portal
    # and emails them a temporary 4-digit PIN. Forces the next login
    # to call POST /auth/change-password before any other endpoint.
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false",
    )
