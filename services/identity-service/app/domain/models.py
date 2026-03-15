from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import List
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared_kernel.db import Base, TimestampMixin


class UserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class RoleType(StrEnum):
    SYSTEM = "system"
    BUSINESS = "business"


class Role(Base, TimestampMixin):
    __tablename__ = "roles"
    __table_args__ = {"schema": "identity"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
    )
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    type: Mapped[RoleType] = mapped_column(String(32), nullable=False, default=RoleType.BUSINESS)

    users: Mapped[List["User"]] = relationship(
        "User",
        secondary="identity.user_roles",
        back_populates="roles",
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("username", name="uq_identity_users_username"),
        UniqueConstraint("email", name="uq_identity_users_email"),
        {"schema": "identity"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
    )
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[UserStatus] = mapped_column(
        String(32),
        default=UserStatus.ACTIVE,
        nullable=False,
    )

    roles: Mapped[List[Role]] = relationship(
        "Role",
        secondary="identity.user_roles",
        back_populates="users",
        lazy="joined",
    )


class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = (
        UniqueConstraint("user_id", "role_id", name="uq_identity_user_roles_user_role"),
        {"schema": "identity"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identity.users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identity.roles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

