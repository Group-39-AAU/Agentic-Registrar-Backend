"""
Undergraduate module — Event handlers.

Subscribes to domain events from other modules, handling them
within the undergraduate bounded context.
"""

from app.shared.enums import ApplicationStatus, UserRole
from app.shared.events import UATCompletedEvent

from sqlalchemy import select


async def handle_uat_completed(event: UATCompletedEvent, *, db) -> None:
    """
    Handle UATCompletedEvent: transition the application
    from UAT_PENDING → UAT_COMPLETED.

    This replaces the direct import of ApplicationService by testing_center.
    """
    from app.modules.undergraduate.models import UndergraduateApplication
    from app.modules.undergraduate.service import ApplicationService
    from app.modules.undergraduate.schemas import ApplicationStatusUpdate

    # Resolve applicant_id from the application
    result = await db.execute(
        select(UndergraduateApplication).where(
            UndergraduateApplication.id == event.application_id
        )
    )
    app_entity = result.scalar_one_or_none()
    if app_entity is None:
        raise ValueError(f"Application {event.application_id} not found")

    svc = ApplicationService(db)
    await svc.change_status(
        event.application_id,
        ApplicationStatusUpdate(
            new_status=ApplicationStatus.UAT_COMPLETED,
            trigger_reason=f"UAT completed — score: {event.score}/100",
        ),
        actor_id=app_entity.applicant_id,
        actor_role=UserRole.SYSTEM,
    )
