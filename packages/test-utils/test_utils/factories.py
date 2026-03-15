from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from contracts import audit as audit_contracts
from contracts import auth as auth_contracts
from contracts import dto as dto_contracts
from contracts import events as events_contracts
from contracts.enums import AuditAction, AuthTokenType


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def user_ref_factory(**overrides: Any) -> dto_contracts.UserRef:
    data: dict[str, Any] = {
        "id": overrides.get("id", str(uuid.uuid4())),
        "display_name": overrides.get("display_name", "Test User"),
        "email": overrides.get("email", "test@example.com"),
    }
    return dto_contracts.UserRef(**data)


def audit_log_entry_factory(**overrides: Any) -> audit_contracts.AuditLogEntry:
    data: dict[str, Any] = {
        "id": overrides.get("id", str(uuid.uuid4())),
        "actor_id": overrides.get("actor_id", str(uuid.uuid4())),
        "action": overrides.get("action", AuditAction.ACCESS),
        "resource_type": overrides.get("resource_type", "test_resource"),
        "resource_id": overrides.get("resource_id", str(uuid.uuid4())),
        "occurred_at": overrides.get("occurred_at", _now()),
        "metadata": overrides.get("metadata", {}),
    }
    return audit_contracts.AuditLogEntry(**data)


def event_envelope_factory(**overrides: Any) -> events_contracts.EventEnvelope:
    data: dict[str, Any] = {
        "id": overrides.get("id", str(uuid.uuid4())),
        "type": overrides.get("type", "test.event"),
        "source": overrides.get("source", "test-suite"),
        "payload": overrides.get("payload", {}),
        "occurred_at": overrides.get("occurred_at", _now()),
        "correlation_id": overrides.get("correlation_id"),
    }
    return events_contracts.EventEnvelope(**data)


def auth_token_payload_factory(**overrides: Any) -> auth_contracts.AuthTokenPayload:
    now = _now()
    data: dict[str, Any] = {
        "sub": overrides.get("sub", str(uuid.uuid4())),
        "iat": overrides.get("iat", now),
        "exp": overrides.get("exp", now),
        "type": overrides.get("type", AuthTokenType.ACCESS),
        "scope": overrides.get("scope", []),
        "roles": overrides.get("roles", []),
    }
    return auth_contracts.AuthTokenPayload(**data)

