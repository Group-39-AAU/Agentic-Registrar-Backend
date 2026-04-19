"""
FastAPI dependencies for authentication and authorization.

get_current_user() extracts the JWT from the Authorization header,
validates it, and returns the authenticated User object.
"""

import uuid
from functools import lru_cache

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import decode_access_token
from app.database.session import get_db
from app.modules.auth.models import User
from app.shared.email.providers.brevo_provider import BrevoProvider
from app.shared.email.service import EmailService

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Extracts and validates the JWT bearer token, then loads the
    corresponding User from the database.

    Raises HTTP 401 if the token is invalid or the user doesn't exist.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    payload = decode_access_token(token)
    if payload is None:
        raise credentials_exception

    user_id_str: str | None = payload.get("sub")
    if user_id_str is None:
        raise credentials_exception

    try:
        user_id = uuid.UUID(user_id_str)
    except ValueError:
        raise credentials_exception

    result = await db.execute(
        select(User).where(User.id == user_id, User.is_deleted == False)  # noqa: E712
    )
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise credentials_exception

    return user


@lru_cache
def _build_email_service() -> EmailService:
    provider = None
    if settings.BREVO_API_KEY and settings.EMAIL_FROM:
        provider = BrevoProvider(
            api_key=settings.BREVO_API_KEY,
            from_email=settings.EMAIL_FROM,
            from_name=settings.EMAIL_FROM_NAME,
            timeout_seconds=settings.EMAIL_TIMEOUT_SECONDS,
        )
    return EmailService(provider=provider, enabled=settings.EMAIL_ENABLED)


def get_email_service() -> EmailService:
    """Dependency provider for shared email service singleton."""
    return _build_email_service()
