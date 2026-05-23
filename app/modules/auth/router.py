"""
Auth module — FastAPI router.

Endpoints: register, login, get current user profile, change password,
forgot-password (request reset link), reset-password (apply the reset).
"""

import logging
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.dependencies import (
    get_current_user_allow_password_change, get_email_service,
)
from app.core.security import create_access_token, hash_password
from app.database.session import get_db
from app.modules.auth.models import User
from app.modules.auth.schemas import (
    ChangePasswordRequest, ForgotPasswordRequest, RegisterRequest,
    ResetPasswordRequest, TokenResponse, UserResponse,
)
from app.modules.auth.service import AuthService
from app.shared.email import EmailService, build_password_reset_email

logger = logging.getLogger(__name__)

# Short-lived reset token — long enough for the user to read the email
# and click through, short enough to limit blast radius if it leaks.
_RESET_TOKEN_TTL = timedelta(minutes=30)
_RESET_TOKEN_PURPOSE = "password_reset"

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", response_model=UserResponse, status_code=201)
async def register(
    data: RegisterRequest,
    db: AsyncSession = Depends(get_db),
    email_service: EmailService = Depends(get_email_service),
):
    """Create a new student account."""
    svc = AuthService(db, email_service=email_service)
    try:
        user = await svc.register(data)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return user


@router.post("/login", response_model=TokenResponse)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    """
    Authenticate with **email** (admission) or **UGR student ID**
    (post-enrollment portal) plus password. Returns a JWT plus a
    ``must_change_password`` flag — when True, the client must call
    ``POST /auth/change-password`` before any other endpoint will
    accept the token.

    Uses OAuth2 form: 'username' field = identifier, 'password' field
    = password.
    """
    svc = AuthService(db)
    try:
        token, must_change = await svc.authenticate(
            form_data.username, form_data.password,
        )
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    return TokenResponse(
        access_token=token, must_change_password=must_change,
    )


@router.get("/me", response_model=UserResponse)
async def get_me(
    current_user: User = Depends(get_current_user_allow_password_change),
):
    """
    Get the currently authenticated user's profile. Reachable even
    while ``must_change_password`` is True so the client can render
    the change-password screen without being locked out.
    """
    return current_user


@router.post("/change-password", status_code=204)
async def change_password(
    data: ChangePasswordRequest,
    current_user: User = Depends(get_current_user_allow_password_change),
    db: AsyncSession = Depends(get_db),
):
    """
    Replace the current password (typically the temporary PIN issued
    at onboarding) with a permanent one. Clears the
    ``must_change_password`` flag so the lockout middleware lets
    every other endpoint through again.
    """
    svc = AuthService(db)
    try:
        await svc.change_password(
            current_user, data.current_password, data.new_password,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return None


@router.post("/forgot-password", status_code=204)
async def forgot_password(
    data: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
    email_service: EmailService = Depends(get_email_service),
):
    """
    Request a password-reset email. **Always returns 204**, regardless
    of whether the email exists — leaking which addresses are registered
    would let an attacker enumerate accounts. If the email maps to a
    real user we mint a 30-minute JWT carrying that user id (purpose
    ``password_reset``) and email a link to ``/reset-password?token=...``.
    """
    result = await db.execute(
        select(User).where(User.email == data.email, User.is_deleted == False)  # noqa: E712
    )
    user = result.scalar_one_or_none()
    if user is None:
        # Don't leak — same 204 response shape as the success path.
        logger.info("forgot-password: no user found for email=%s (silent 204)", data.email)
        return None

    token = create_access_token(
        data={"sub": str(user.id), "purpose": _RESET_TOKEN_PURPOSE},
        expires_delta=_RESET_TOKEN_TTL,
    )
    base = settings.PUBLIC_APP_BASE_URL.rstrip("/")
    # The reset page lives on the FRONTEND. Backend's PUBLIC_APP_BASE_URL
    # points at the API host by default — for a fresh install the user
    # may need to deploy the frontend at the same origin or override
    # this in env. For now we use the same base; the link is generic
    # enough that copy-pasting it into the browser works either way.
    reset_url = f"{base}/reset-password?token={token}"

    try:
        await email_service.send(
            build_password_reset_email(
                to_email=user.email,
                first_name=user.first_name or "",
                reset_url=reset_url,
                valid_minutes=int(_RESET_TOKEN_TTL.total_seconds() // 60),
            )
        )
    except Exception:
        # Send failures shouldn't surface to the caller (same anti-enumeration
        # reasoning) but they MUST be logged so ops can investigate.
        logger.exception("forgot-password: email send failed user_id=%s", user.id)

    return None


@router.post("/reset-password", status_code=204)
async def reset_password(
    data: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Apply a password reset using the JWT issued by ``/auth/forgot-password``.
    The token must be unexpired and carry ``purpose=password_reset``.
    On success the user can log in immediately with the new password,
    and ``must_change_password`` is cleared (the reset replaces any
    pending PIN flow).
    """
    try:
        payload = jwt.decode(
            data.token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
    except JWTError:
        raise HTTPException(status_code=400, detail="Invalid or expired reset link.")

    if payload.get("purpose") != _RESET_TOKEN_PURPOSE:
        raise HTTPException(status_code=400, detail="Invalid or expired reset link.")

    sub = payload.get("sub")
    try:
        user_id = uuid.UUID(str(sub))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid or expired reset link.")

    result = await db.execute(
        select(User).where(User.id == user_id, User.is_deleted == False)  # noqa: E712
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=400, detail="Invalid or expired reset link.")

    user.hashed_password = hash_password(data.new_password)
    user.must_change_password = False
    await db.commit()
    return None
