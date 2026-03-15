from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import httpx


async def create_async_client(app: Any) -> httpx.AsyncClient:
    """Create an `httpx.AsyncClient` for a given ASGI app."""

    return httpx.AsyncClient(app=app, base_url="http://testserver")


async def lifespan_client(app: Any) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Async context manager-like helper that handles client lifespan."""

    async with httpx.AsyncClient(app=app, base_url="http://testserver") as client:
        yield client

