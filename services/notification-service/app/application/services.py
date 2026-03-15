from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from ..domain.models import Channel as ModelChannel
from ..domain.models import Notification, NotificationStatus
from ..domain.schemas import Channel, NotificationOut, SendNotificationRequest
from ..infrastructure.providers import NotificationProvider, get_provider
from ..infrastructure.repositories import NotificationRepository


class NotificationService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = NotificationRepository(session)

    def send(self, req: SendNotificationRequest) -> NotificationOut:
        provider: NotificationProvider = get_provider(req.channel)
        notif = Notification(
            channel=ModelChannel(req.channel),
            recipient=req.recipient,
            subject=req.subject,
            body=req.body,
            template_key=req.template_key,
            status=NotificationStatus.PENDING,
            retry_count=0,
        )
        self._repo.add(notif)
        try:
            provider.send(
                channel=req.channel,
                recipient=req.recipient,
                subject=req.subject,
                body=req.body,
            )
            notif.status = NotificationStatus.SENT
        except Exception as exc:  # pragma: no cover - simple failure path
            notif.status = NotificationStatus.FAILED
            notif.retry_count += 1
            notif.last_error = str(exc)
        self._session.commit()
        return self._to_out(notif)

    def get_notification(self, notification_id: str) -> NotificationOut:
        notif = self._repo.get(uuid.UUID(notification_id))
        return self._to_out(notif)

    @staticmethod
    def _to_out(notif: Notification) -> NotificationOut:
        return NotificationOut(
            id=str(notif.id),
            channel=Channel(notif.channel),
            recipient=notif.recipient,
            subject=notif.subject,
            body=notif.body,
            template_key=notif.template_key,
            status=NotificationStatus(notif.status),
            retry_count=notif.retry_count,
            last_error=notif.last_error,
        )

