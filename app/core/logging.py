"""
Structured logging and audit trail infrastructure.

Provides:
    - ``get_logger(name)``      → Named logger for module-specific logging.
    - ``write_audit_log(...)``  → Writes structured JSON audit entries to stdout.
      Services will ALSO persist these to the ``system_audit_logs`` table
      within the same transaction for database-level compliance.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

# ── Logger Setup ──────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Dedicated audit logger for structured audit lines
_audit_logger = logging.getLogger("app.audit")


def get_logger(name: str) -> logging.Logger:
    """Returns a named logger (e.g., ``get_logger('undergraduate')``)."""
    return logging.getLogger(name)


# ── Audit Log Helper ─────────────────────────────────────────

def write_audit_log(
    action: str,
    actor_role: str,
    resource_type: str,
    resource_id: uuid.UUID,
    actor_id: Optional[uuid.UUID] = None,
    decision: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    """
    Emit a structured JSON audit entry to the application log.

    This is the *stdout side* of the audit trail. The service layer is
    responsible for also inserting a ``SystemAuditLog`` row in the same
    database transaction to satisfy the persistence requirement.
    """
    entry = {
        "audit_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "actor_id": str(actor_id) if actor_id else None,
        "actor_role": actor_role,
        "resource_type": resource_type,
        "resource_id": str(resource_id),
        "decision": decision,
        "metadata": metadata or {},
    }
    _audit_logger.info(json.dumps(entry))
