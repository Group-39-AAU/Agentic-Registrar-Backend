from __future__ import annotations

from typing import Any, Mapping

import httpx

from shared_kernel.correlation import get_correlation_id


class GatewayHttpClient:
    """Shared async HTTP client for proxying requests to backend services."""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=10.0)

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json: Any | None = None,
        params: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        base_headers: dict[str, str] = {}
        if headers:
            base_headers.update(headers)
        correlation_id = get_correlation_id()
        if correlation_id is not None:
            base_headers.setdefault("X-Request-ID", correlation_id)
        return await self._client.request(
            method=method,
            url=url,
            headers=base_headers,
            json=json,
            params=params,
        )


async_client = GatewayHttpClient()

