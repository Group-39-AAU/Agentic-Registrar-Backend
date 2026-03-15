from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from .auth import get_current_principal
from ..config.settings import get_settings
from ..infrastructure.http_client import async_client

router = APIRouter()


def _build_target_url(base: str, path: str) -> str:
    return f"{base.rstrip('/')}/{path.lstrip('/')}"


async def _proxy(
    request: Request,
    base_url: str,
    *,
    require_auth: bool = False,
    principal: Dict[str, Any] | None = None,
) -> Any:
    body = None
    if request.method in {"POST", "PUT", "PATCH"}:
        body = await request.json()
    headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
    url = _build_target_url(base_url, request.url.path.split("/", 2)[-1])
    response = await async_client.request(
        method=request.method,
        url=url,
        headers=headers,
        json=body,
        params=dict(request.query_params),
    )
    return response


@router.api_route("/auth/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_auth(request: Request, path: str) -> Any:
    settings = get_settings()
    response = await _proxy(request, str(settings.identity_base_url))
    return JSONResponse(
        content=response.json(),
        status_code=response.status_code,
        headers={k: v for k, v in response.headers.items() if k.lower() not in {"content-length", "transfer-encoding", "connection"}},
    )


@router.api_route("/undergrad/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_undergrad(
    request: Request,
    path: str,
    principal: Dict[str, Any] = Depends(get_current_principal),
) -> Any:
    settings = get_settings()
    response = await _proxy(
        request,
        str(settings.undergrad_base_url),
        require_auth=True,
        principal=principal,
    )
    return JSONResponse(
        content=response.json(),
        status_code=response.status_code,
        headers={k: v for k, v in response.headers.items() if k.lower() not in {"content-length", "transfer-encoding", "connection"}},
    )


@router.api_route("/graduate/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_graduate(
    request: Request,
    path: str,
    principal: Dict[str, Any] = Depends(get_current_principal),
) -> Any:
    settings = get_settings()
    response = await _proxy(
        request,
        str(settings.graduate_base_url),
        require_auth=True,
        principal=principal,
    )
    return JSONResponse(
        content=response.json(),
        status_code=response.status_code,
        headers={k: v for k, v in response.headers.items() if k.lower() not in {"content-length", "transfer-encoding", "connection"}},
    )


@router.api_route("/courses/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_courses(
    request: Request,
    path: str,
    principal: Dict[str, Any] = Depends(get_current_principal),
) -> Any:
    settings = get_settings()
    response = await _proxy(
        request,
        str(settings.courses_base_url),
        require_auth=True,
        principal=principal,
    )
    return JSONResponse(
        content=response.json(),
        status_code=response.status_code,
        headers={k: v for k, v in response.headers.items() if k.lower() not in {"content-length", "transfer-encoding", "connection"}},
    )


@router.api_route("/documents/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_documents(
    request: Request,
    path: str,
    principal: Dict[str, Any] = Depends(get_current_principal),
) -> Any:
    settings = get_settings()
    response = await _proxy(
        request,
        str(settings.documents_base_url),
        require_auth=True,
        principal=principal,
    )
    return JSONResponse(
        content=response.json(),
        status_code=response.status_code,
        headers={k: v for k, v in response.headers.items() if k.lower() not in {"content-length", "transfer-encoding", "connection"}},
    )


@router.api_route("/notifications/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_notifications(
    request: Request,
    path: str,
    principal: Dict[str, Any] = Depends(get_current_principal),
) -> Any:
    settings = get_settings()
    response = await _proxy(
        request,
        str(settings.notifications_base_url),
        require_auth=True,
        principal=principal,
    )
    return JSONResponse(
        content=response.json(),
        status_code=response.status_code,
        headers={k: v for k, v in response.headers.items() if k.lower() not in {"content-length", "transfer-encoding", "connection"}},
    )

