from __future__ import annotations

from http import HTTPStatus

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from contracts.responses import ResponseEnvelope
from shared_kernel.api import make_error_response
from shared_kernel.exceptions import NotFoundError

from ...application.services import NotificationService
from ...domain.schemas import NotificationOut, SendNotificationRequest
from ...infrastructure.db import get_db_session

router = APIRouter(prefix="/notifications", tags=["notifications"])


def get_notification_service(session: Session = Depends(get_db_session)) -> NotificationService:
    return NotificationService(session=session)


@router.post("/send", response_model=ResponseEnvelope[NotificationOut])
def send_notification(
    req: SendNotificationRequest,
    service: NotificationService = Depends(get_notification_service),
):
    notif = service.send(req)
    return ResponseEnvelope[NotificationOut](data=notif)


@router.get("/{notification_id}", response_model=ResponseEnvelope[NotificationOut])
def get_notification(
    notification_id: str,
    service: NotificationService = Depends(get_notification_service),
):
    try:
        notif = service.get_notification(notification_id)
    except NotFoundError as exc:
        error = make_error_response(
            HTTPStatus.NOT_FOUND,
            code="notification_not_found",
            message=str(exc),
        )
        raise HTTPException(status_code=error.status, detail=error.error.message)
    return ResponseEnvelope[NotificationOut](data=notif)

