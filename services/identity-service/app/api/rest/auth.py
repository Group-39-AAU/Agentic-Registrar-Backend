from __future__ import annotations

from http import HTTPStatus

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from contracts.responses import ResponseEnvelope

from shared_kernel.api import make_error_response
from shared_kernel.exceptions import UnauthorizedError
from shared_kernel.security import PBKDF2PasswordHasher

from ...application.services import AuthService
from ...config.settings import get_settings
from ...domain.schemas import (
    LoginRequest,
    TokenIntrospectRequest,
    TokenIntrospectResponse,
    TokenResponse,
    UserOut,
)
from ...infrastructure.db import get_db_session
from ...infrastructure.security import create_jwt_helper

router = APIRouter(prefix="/auth", tags=["auth"])

security = HTTPBearer(auto_error=False)


def get_auth_service(session: Session = Depends(get_db_session)) -> AuthService:
    hasher = PBKDF2PasswordHasher()
    return AuthService(session=session, hasher=hasher)


def get_jwt_helper():
    settings = get_settings()
    return create_jwt_helper(
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        minutes=settings.jwt_access_expires_minutes,
    )


@router.post("/login", response_model=ResponseEnvelope[TokenResponse])
def login(
    payload: LoginRequest,
    auth_service: AuthService = Depends(get_auth_service),
):
    try:
        user = auth_service.authenticate(payload.username, payload.password)
    except UnauthorizedError as exc:
        error = make_error_response(
            HTTPStatus.UNAUTHORIZED,
            code="invalid_credentials",
            message=str(exc),
        )
        raise HTTPException(status_code=error.status, detail=error.error.message)

    jwt_helper = get_jwt_helper()
    token_payload = auth_service.build_access_token_payload_for_user(user)
    access_token = jwt_helper.encode(token_payload)
    envelope = ResponseEnvelope[TokenResponse](data=TokenResponse(access_token=access_token))
    return envelope


def _decode_token(token: str) -> dict:
    helper = get_jwt_helper()
    return helper.decode(token)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(status_code=HTTPStatus.UNAUTHORIZED, detail="Missing credentials")
    try:
        payload = _decode_token(creds.credentials)
    except Exception:
        raise HTTPException(status_code=HTTPStatus.UNAUTHORIZED, detail="Invalid token")
    return payload


@router.post("/token/introspect", response_model=TokenIntrospectResponse)
def introspect_token(body: TokenIntrospectRequest):
    try:
        payload = _decode_token(body.token)
    except Exception:
        return TokenIntrospectResponse(active=False)
    sub = str(payload.get("sub")) if payload.get("sub") is not None else None
    roles = [str(r) for r in payload.get("roles", [])]
    return TokenIntrospectResponse(active=True, sub=sub, roles=roles)


@router.get("/me", response_model=UserOut)
def get_me(
    token_payload: dict = Depends(get_current_user),
):
    # For now, echo token subject and roles; services can expand this to DB lookups later.
    user = UserOut(
        id=str(token_payload.get("sub")),
        username=str(token_payload.get("sub")),
        email="placeholder@example.com",
        is_active=True,
        roles=[{"id": r, "name": r} for r in token_payload.get("roles", [])],
    )
    return user

