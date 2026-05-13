"""
Abstract base for all autonomous agents in the Agentic Registrar system.

Defines the contract every concrete agent (intake, ranking, curriculum
compliance, assessment validation, academic standing, etc.) inherits from.
The contract mirrors the BaseAgent class in SDS Tables 85–86 verbatim:

    - protected ``agent_id: str``         (e.g. "AGENT_CCA_01")
    - protected ``status: AgentStatus``   (IDLE / BUSY / WAITING_HUMAN / ERROR)
    - public    ``get_status()``
    - protected ``_log_action(...)``      writes to system_audit_logs +
                                          stdout JSON via write_audit_log
    - abstract  ``process_task(...)``     entry point overridden per agent

Concrete agents in app/modules/<module>/agents/ extend this class.
"""

import uuid
from abc import ABC, abstractmethod
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import write_audit_log
from app.shared.audit.models import SystemAuditLog
from app.shared.enums import AgentStatus, UserRole


class BaseAgent(ABC):
    """
    Abstract base class every autonomous agent must extend.

    Subclasses implement :meth:`process_task`. Use :meth:`set_status` to
    flip the lifecycle state and :meth:`_log_action` to record decisions
    to both the system audit log table and the structured stdout stream.
    """

    def __init__(
        self,
        agent_id: str,
        status: AgentStatus = AgentStatus.IDLE,
    ) -> None:
        self._agent_id = agent_id
        self._status = status

    # ── Public accessors (SDS Table 85) ──────────────────────────────

    @property
    def agent_id(self) -> str:
        """Stable identifier for this agent instance (SDS invariant Table 86)."""
        return self._agent_id

    def get_status(self) -> AgentStatus:
        """Returns the current lifecycle state of the agent."""
        return self._status

    def set_status(self, status: AgentStatus) -> None:
        """Transition the agent into a new lifecycle state."""
        self._status = status

    # ── Audit hook (SDS Table 85) ────────────────────────────────────

    async def _log_action(
        self,
        session: AsyncSession,
        action: str,
        resource_type: str,
        resource_id: uuid.UUID,
        decision: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> SystemAuditLog:
        """
        Record an agent decision to both the audit-log table and the
        structured stdout stream. Caller commits the surrounding
        transaction; the row is only ``session.add()``-ed here so that
        callers can keep the agent decision atomic with the workflow
        state change that triggered it.
        """
        payload: dict[str, Any] = {"agent_id": self._agent_id}
        if metadata:
            payload.update(metadata)

        log_row = SystemAuditLog(
            actor_id=None,
            actor_role=UserRole.AGENT.value,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            decision=decision,
            metadata_payload=payload,
        )
        session.add(log_row)

        write_audit_log(
            action=action,
            actor_role=UserRole.AGENT.value,
            resource_type=resource_type,
            resource_id=resource_id,
            decision=decision,
            metadata=payload,
        )
        return log_row

    # ── Workflow entry point (SDS Table 85) ──────────────────────────

    @abstractmethod
    async def process_task(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """
        The single entry point every concrete agent must implement.
        Inputs and outputs are agent-specific; the SDS leaves the shape
        deliberately open so each agent can define its own contract.
        """
        raise NotImplementedError
