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
