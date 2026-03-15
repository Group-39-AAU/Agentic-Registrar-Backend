from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared_kernel.db import Base, TimestampMixin


class Channel(StrEnum):
    EMAIL = "email"
    SMS = "sms"


class NotificationStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"
    __table_args__ = {"schema": "notifications"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    channel: Mapped[Channel] = mapped_column(String(32), nullable=False)
    recipient: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    template_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[NotificationStatus] = mapped_column(
        String(32),
        nullable=False,
        default=NotificationStatus.PENDING,
    )
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

