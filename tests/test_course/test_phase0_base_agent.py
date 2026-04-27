"""
Phase 0 — BaseAgent and CourseBaseAgent contract tests.

Confirms the SDS Tables 85–86 contract: agent_id is settable and
read-back-able, status flips through the AgentStatus enum, and
_log_action writes a row to system_audit_logs with the actor_role
set to AGENT and the agent_id preserved in the metadata payload.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.ai.base import BaseAgent
from app.modules.course.agents import CourseBaseAgent
from app.shared.audit.models import SystemAuditLog
from app.shared.enums import AgentStatus, UserRole


# ── Concrete subclasses for testing ──────────────────────────────


class _ConcreteBase(BaseAgent):
    async def process_task(self, input_data):
        return {"echo": input_data}


class _ConcreteCourse(CourseBaseAgent):
    async def process_task(self, input_data):
        return {"agent_id": self.agent_id}


# ── Tests ────────────────────────────────────────────────────────


def test_base_agent_status_lifecycle():
    a = _ConcreteBase(agent_id="AGENT_TEST_01")
    assert a.agent_id == "AGENT_TEST_01"
    assert a.get_status() == AgentStatus.IDLE
    a.set_status(AgentStatus.BUSY)
    assert a.get_status() == AgentStatus.BUSY
    a.set_status(AgentStatus.WAITING_HUMAN)
    assert a.get_status() == AgentStatus.WAITING_HUMAN


async def test_base_agent_log_action_writes_audit_row(async_session):
    agent = _ConcreteBase(agent_id="AGENT_TEST_02")
    target_id = uuid.uuid4()

    await agent._log_action(
        session=async_session,
        action="course_management.test.dispatched",
        resource_type="Course",
        resource_id=target_id,
        decision="OK",
        metadata={"note": "smoke"},
    )
    await async_session.commit()

    rows = (
        await async_session.execute(
            select(SystemAuditLog).where(SystemAuditLog.resource_id == target_id)
        )
    ).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.actor_role == UserRole.AGENT.value
    assert row.actor_id is None
    assert row.action == "course_management.test.dispatched"
    assert row.decision == "OK"
    assert row.metadata_payload["agent_id"] == "AGENT_TEST_02"
    assert row.metadata_payload["note"] == "smoke"


async def test_course_base_agent_extends_contract(async_session):
    agent = _ConcreteCourse(agent_id="AGENT_CCA_TEST")
    assert isinstance(agent, BaseAgent)
    assert agent.agent_id == "AGENT_CCA_TEST"

    # ground_in_policy is a phase-1 no-op until pgvector lands.
    snippets = await agent.ground_in_policy("max credit load")
    assert snippets == []

    out = await agent.process_task({"any": "thing"})
    assert out == {"agent_id": "AGENT_CCA_TEST"}


def test_base_agent_is_abstract_and_cannot_be_instantiated_directly():
    """
    Concrete agents must implement process_task; instantiating the
    abstract BaseAgent itself must fail.
    """
    import pytest
    with pytest.raises(TypeError):
        BaseAgent(agent_id="AGENT_ABS")  # type: ignore[abstract]
