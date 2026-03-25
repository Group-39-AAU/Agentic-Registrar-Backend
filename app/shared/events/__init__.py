"""
Domain Events — Lightweight in-process event bus.

Provides a simple publish/subscribe mechanism for decoupling modules.
Each module can publish events without knowing who handles them,
and subscribe to events without importing the publisher.

Usage:
    # Publisher (e.g., testing_center)
    from app.shared.events import publish
    await publish(UATCompletedEvent(application_id=..., score=85.0))

    # Subscriber (e.g., undergraduate)
    from app.shared.events import subscribe
    subscribe("UATCompletedEvent", handle_uat_completed)
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Coroutine
from uuid import UUID, uuid4

logger = logging.getLogger("events")

# ── Event Base ──

@dataclass
class DomainEvent:
    """Base class for all domain events."""
    event_id: UUID = field(default_factory=uuid4)
    timestamp: datetime = field(default_factory=datetime.utcnow)


# ── Concrete Events ──

@dataclass
class UATCompletedEvent(DomainEvent):
    """Published when a UAT record is completed with a score."""
    application_id: UUID = field(default_factory=uuid4)
    applicant_id: UUID = field(default_factory=uuid4)
    uat_id: str = ""
    score: float = 0.0


# ── Event Bus ──

# Registry: event_type_name → list of async handler functions
_handlers: dict[str, list[Callable[..., Coroutine]]] = {}


def subscribe(event_type: str, handler: Callable[..., Coroutine]) -> None:
    """Register an async handler for a given event type."""
    _handlers.setdefault(event_type, []).append(handler)
    logger.info("Subscribed %s to %s", handler.__name__, event_type)


async def publish(event: DomainEvent, **kwargs: Any) -> None:
    """
    Publish an event to all registered handlers.

    Extra kwargs (e.g., db session) are forwarded to handlers.
    """
    event_type = type(event).__name__
    handlers = _handlers.get(event_type, [])

    if not handlers:
        logger.warning("No handlers for event: %s", event_type)
        return

    for handler in handlers:
        try:
            await handler(event, **kwargs)
            logger.info("Event %s handled by %s", event_type, handler.__name__)
        except Exception as e:
            logger.error(
                "Handler %s failed for event %s: %s",
                handler.__name__, event_type, e,
                exc_info=True,
            )
