from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import timedelta
from typing import Any, Dict

from contracts.auth import AuthTokenPayload

from shared_kernel.security import JWTHelper, JWTConfig, PBKDF2PasswordHasher, compute_expiry


class StdlibJWTHelper(JWTHelper):
    """Minimal HS256 JWT implementation using the Python standard library."""

    def __init__(self, config: JWTConfig) -> None:
        self.config = config

    def _b64url_encode(self, data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    def _b64url_decode(self, data: str) -> bytes:
        padding = "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode(data + padding)

    def encode(self, payload: Dict[str, Any], *, expires_delta: timedelta | None = None) -> str:
        exp_delta = expires_delta or self.config.access_token_ttl
        exp = compute_expiry(exp_delta)
        payload = dict(payload)
        payload["exp"] = int(exp.timestamp())

        header = {"alg": self.config.algorithm, "typ": "JWT"}
        header_b64 = self._b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        payload_b64 = self._b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
        signature = hmac.new(
            self.config.secret.encode("utf-8"),
            signing_input,
            hashlib.sha256,
        ).digest()
        signature_b64 = self._b64url_encode(signature)
        return f"{header_b64}.{payload_b64}.{signature_b64}"

    def decode(self, token: str) -> Dict[str, Any]:
        try:
            header_b64, payload_b64, signature_b64 = token.split(".")
        except ValueError as exc:  # pragma: no cover - simple schema error
            raise ValueError("Invalid token") from exc

        signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
        expected_sig = hmac.new(
            self.config.secret.encode("utf-8"),
            signing_input,
            hashlib.sha256,
        ).digest()
        actual_sig = self._b64url_decode(signature_b64)
        if not hmac.compare_digest(expected_sig, actual_sig):
            raise ValueError("Invalid signature")

        payload_bytes = self._b64url_decode(payload_b64)
        data = json.loads(payload_bytes.decode("utf-8"))
        return data


def create_password_hasher() -> PBKDF2PasswordHasher:
    return PBKDF2PasswordHasher()


def create_jwt_helper(secret: str, algorithm: str, minutes: int) -> StdlibJWTHelper:
    config = JWTConfig(
        secret=secret,
        algorithm=algorithm,
        access_token_ttl=timedelta(minutes=minutes),
    )
    return StdlibJWTHelper(config=config)


def build_access_token_payload(*, user_id: str, roles: list[str]) -> Dict[str, Any]:
    token = AuthTokenPayload.model_construct(
        sub=user_id,
        iat=compute_expiry(timedelta(seconds=0)),
        exp=compute_expiry(timedelta(minutes=0)),
        scope=[],
        roles=roles,
    )
    data = token.model_dump()
    # Convert datetimes to timestamps; StdlibJWTHelper will override exp later.
    data["iat"] = int(token.iat.timestamp())
    data["exp"] = int(token.exp.timestamp())
    return data

