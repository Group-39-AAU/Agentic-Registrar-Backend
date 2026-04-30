"""
JWT token creation and password hashing utilities.

Uses python-jose for JWT and bcrypt directly for password hashing.
We talk to bcrypt directly rather than through passlib because
passlib 1.7.4's bcrypt-version self-detection breaks on bcrypt 4.x
(the new ``__about__`` removal trips an internal probe with a
>72-byte test password). bcrypt's own API is small and stable, so
calling it directly is the durable fix.
"""

from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings


# bcrypt has always silently truncated to 72 bytes; bcrypt 4.x raises
# instead. Truncate explicitly so callers behave the same on either
# version. UTF-8 multibyte chars count by byte, not by char.
_BCRYPT_MAX_BYTES = 72


def _truncate_to_bcrypt_limit(password: str) -> bytes:
    """Encode and truncate to bcrypt's 72-byte payload window."""
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt (12 rounds)."""
    return bcrypt.hashpw(
        _truncate_to_bcrypt_limit(password),
        bcrypt.gensalt(rounds=12),
    ).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(
            _truncate_to_bcrypt_limit(plain_password),
            hashed_password.encode("utf-8"),
        )
    except (ValueError, TypeError):
        # Malformed hash or bytes input — treat as a verification failure
        # rather than a 500.
        return False


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """Create a JWT access token with the given payload and expiration."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    """Decode and validate a JWT access token. Returns payload or None."""
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        return None
