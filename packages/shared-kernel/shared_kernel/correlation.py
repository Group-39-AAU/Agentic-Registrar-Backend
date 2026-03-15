from __future__ import annotations

import contextvars
import uuid
from typing import Callable

from starlette.types import ASGIApp, Receive, Scope, Send

_correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)


def get_correlation_id() -> str | None:
    """Return the current correlation ID, if any."""

    return _correlation_id.get()


def set_correlation_id(value: str | None) -> None:
    """Set the current correlation ID for this context."""

    _correlation_id.set(value)


class CorrelationIdMiddleware:
    """ASGI middleware that manages a request correlation ID.

    - Reads an incoming `X-Request-ID` header if present.
    - Otherwise generates a new UUID4.
    - Exposes the ID via `get_correlation_id`.
    - Echoes the ID back on the response header `X-Request-ID`.
    """

    def __init__(self, app: ASGIApp, header_name: str = "x-request-id") -> None:
        self.app = app
        self.header_name = header_name.lower()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope["headers"]}
        incoming = headers.get(self.header_name)
        correlation_id = incoming or str(uuid.uuid4())

        token = _correlation_id.set(correlation_id)

        async def send_wrapper(message):
            if message.get("type") == "http.response.start":
                headers_list = list(message.get("headers", []))
                headers_list.append((b"X-Request-ID", correlation_id.encode("latin1")))
                message["headers"] = headers_list
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            _correlation_id.reset(token)


MiddlewareFactory = Callable[[ASGIApp], CorrelationIdMiddleware]


def correlation_id_middleware_factory() -> MiddlewareFactory:
    """Factory for convenient integration with FastAPI/Starlette."""

    def factory(app: ASGIApp) -> CorrelationIdMiddleware:
        return CorrelationIdMiddleware(app)

    return factory

