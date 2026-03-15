from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, EmailStr, Field


class Channel(str, StrEnum):
    EMAIL = "email"
    SMS = "sms"


class NotificationStatus(str, StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class SendNotificationRequest(BaseModel):
    channel: Channel
    recipient: str
    subject: str | None = None
    body: str
    template_key: str | None = None


class NotificationOut(BaseModel):
    id: str
    channel: Channel
    recipient: str
    subject: str | None = None
    body: str
    template_key: str | None = None
    status: NotificationStatus
    retry_count: int
    last_error: str | None = None

