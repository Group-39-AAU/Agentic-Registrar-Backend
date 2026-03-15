from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol


class PasswordHasher(Protocol):
    """Abstraction for password hashing."""

    def hash(self, password: str) -> str: ...

    def verify(self, password: str, hashed: str) -> bool: ...


class PBKDF2PasswordHasher:
    """Simple PBKDF2 hasher using only the standard library.

    This is suitable for production when configured with strong parameters, but
    may be replaced with a dedicated library (e.g. argon2) if desired.
    """

    def __init__(self, iterations: int = 390_000, salt_size: int = 16) -> None:
        self.iterations = iterations
        self.salt_size = salt_size

    def hash(self, password: str) -> str:
        salt = os.urandom(self.salt_size)
        dk = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            self.iterations,
        )
        return f"pbkdf2_sha256${self.iterations}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"

    def verify(self, password: str, hashed: str) -> bool:
        try:
            algorithm, iterations_str, salt_b64, hash_b64 = hashed.split("$", 3)
        except ValueError:
            return False
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_str)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            iterations,
        )
        return hmac.compare_digest(dk, expected)


class JWTError(Exception):
    """Base error for JWT-related failures."""


@dataclass(slots=True)
class JWTConfig:
    """Configuration required to encode/decode JWT tokens.

    This intentionally does not depend on a specific JWT implementation; concrete
    services can provide an implementation using their library of choice.
    """

    secret: str
    algorithm: str = "HS256"
    access_token_ttl: timedelta = timedelta(minutes=60)


class JWTHelper(Protocol):
    """Abstraction hook for JWT operations."""

    config: JWTConfig

    def encode(self, payload: dict[str, Any], *, expires_delta: timedelta | None = None) -> str: ...

    def decode(self, token: str) -> dict[str, Any]: ...


def compute_expiry(ttl: timedelta) -> datetime:
    """Compute an expiry timestamp in UTC."""

    return datetime.now(tz=timezone.utc) + ttl

