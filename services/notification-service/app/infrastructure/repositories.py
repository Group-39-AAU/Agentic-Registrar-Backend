from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from shared_kernel.exceptions import NotFoundError

from ..domain.models import Notification


class NotificationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, notification: Notification) -> None:
        self._session.add(notification)

    def get(self, notification_id: uuid.UUID) -> Notification:
        stmt = select(Notification).where(Notification.id == notification_id)
        notif = self._session.execute(stmt).scalars().first()
        if notif is None:
            raise NotFoundError(f"Notification {notification_id} not found")
        return notif

