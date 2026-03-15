from __future__ import annotations

from http import HTTPStatus
from typing import Any, Dict

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import httpx

from shared_kernel.correlation import get_correlation_id

from ..config.settings import get_settings


security = HTTPBearer(auto_error=False)


async def introspect_token(token: str) -> Dict[str, Any]:
    settings = get_settings()
    url = f"{settings.identity_base_url}/auth/token/introspect"
    headers: dict[str, str] = {}
    correlation_id = get_correlation_id()
    if correlation_id is not None:
        headers["X-Request-ID"] = correlation_id
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.post(url, json={"token": token}, headers=headers)
    if resp.status_code != HTTPStatus.OK:
        raise HTTPException(status_code=HTTPStatus.UNAUTHORIZED, detail="Token introspection failed")
    data = resp.json()
    if not data.get("active"):
        raise HTTPException(status_code=HTTPStatus.UNAUTHORIZED, detail="Inactive token")
    return data


async def get_current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> Dict[str, Any]:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=HTTPStatus.UNAUTHORIZED, detail="Missing credentials")
    return await introspect_token(credentials.credentials)

