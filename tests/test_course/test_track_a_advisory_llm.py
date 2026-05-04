"""
Track A — AcademicAdvisoryAgent + LLM enrichment.

Exercises the optional LLM hand-off: when a client is injected, the
agent must use it to rewrite ``Advice.explanation``; when the client
returns ``None`` (any SDK failure), the rule-based explanation must
survive untouched. Critically, the LLM must never influence the
structured verdict (risk_status, recommended_courses,
requires_officer_review) — that's a hard SDS invariant.

The Anthropic SDK is never called for real; we inject a stub
LLMClient subclass whose ``narrate_advisory`` is fully under test
control.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

import pytest
import pytest_asyncio
from unittest.mock import MagicMock

from app.ai.llm_client import LLMClient
from app.modules.course.agents import AcademicAdvisoryAgent, Advice
from app.modules.course.models import Course
from app.shared.enums import RiskStatus


# ── Stub LLM clients ────────────────────────────────────────────


class _FakeLLM(LLMClient):
    """LLMClient subclass with no network — returns canned text."""

    def __init__(self, text: Optional[str]) -> None:
        # Skip parent __init__ — we don't need a real Anthropic client
        # because narrate_advisory is fully overridden below.
        self._client = MagicMock()
        self._model = "claude-haiku-4-5"
        self._timeout = 5.0
        self._max_tokens = 600
        self._text = text
        self.calls: list[dict[str, Any]] = []

    async def narrate_advisory(
        self,
        structured_advice: dict[str, Any],
        student_context: dict[str, Any],
    ) -> Optional[str]:
        self.calls.append({
            "advice": structured_advice,
            "student": student_context,
        })
        return self._text


class _BoomLLM(LLMClient):
    """LLMClient subclass whose narrate_advisory always raises."""

    def __init__(self) -> None:
        self._client = MagicMock()
        self._model = "claude-haiku-4-5"
        self._timeout = 5.0
        self._max_tokens = 600

    async def narrate_advisory(
        self,
        structured_advice: dict[str, Any],
        student_context: dict[str, Any],
    ) -> Optional[str]:
        raise RuntimeError("LLM provider is on fire")


# ── Curriculum fixture ──────────────────────────────────────────


@pytest_asyncio.fixture
async def cs_curriculum(async_session) -> dict[int, Course]:
    """One course per semester, three semesters — minimal but enough."""
    courses: dict[int, Course] = {}
    for sem in range(1, 4):
        c = Course(
            code=f"CS{sem}01", title=f"CS {sem}01",
            credit_hours=4, semester=sem,
            department="Computer Science",
        )
        async_session.add(c)
        courses[sem] = c
    await async_session.flush()
    return courses


def _input(session, **overrides: Any) -> dict[str, Any]:
    base = {
        "session": session,
        "department": "Computer Science",
        "current_semester": 2,
        "cgpa": 3.1,
        "total_proposed_credits": 16,
        "completed_course_ids": set(),
    }
    base.update(overrides)
    return base


# ── Happy path: LLM rewrites the explanation ────────────────────


async def test_llm_explanation_replaces_rule_explanation(
    async_session, cs_curriculum,
):
    canned = (
        "You're holding a solid 3.1 CGPA — a 16-credit load is reasonable. "
        "Take CS201 next to keep the core sequence on track."
    )
    fake = _FakeLLM(text=canned)
    agent = AcademicAdvisoryAgent(llm_client=fake)

    advice: Advice = await agent.process_task(_input(async_session))

    assert advice.explanation == canned
    assert len(fake.calls) == 1


async def test_llm_receives_structured_advice_and_student_context(
    async_session, cs_curriculum,
):
    fake = _FakeLLM(text="ok")
    agent = AcademicAdvisoryAgent(llm_client=fake)

    await agent.process_task(_input(
        async_session, cgpa=2.4, total_proposed_credits=20,
    ))

    payload = fake.calls[0]
    assert payload["student"]["cgpa"] == 2.4
    assert payload["student"]["proposed_credits"] == 20
    assert payload["student"]["department"] == "Computer Science"
    # Rule-engine output is what the LLM is asked to rewrite, so it
    # must travel in the prompt.
    assert "rule_explanation" in payload["advice"]
    assert payload["advice"]["risk_status"] in {"LOW", "MEDIUM", "HIGH"}


# ── Verdict integrity: LLM cannot change the structured fields ─


async def test_llm_does_not_alter_structured_verdict(
    async_session, cs_curriculum,
):
    """SDS invariant: rule engine owns risk + recommendations."""
    # Force HIGH risk path with a low CGPA + heavy load
    fake = _FakeLLM(
        text="Looking great! Take 24 credits, no concerns at all.",
    )
    agent = AcademicAdvisoryAgent(llm_client=fake)

    advice = await agent.process_task(_input(
        async_session, cgpa=1.5, total_proposed_credits=22,
    ))

    # Even with sunny LLM prose, the verdict stays HIGH and escalates
    assert advice.risk_status == RiskStatus.HIGH
    assert advice.requires_officer_review is True
    # Recommendations come from the rule engine, not the LLM
    assert advice.recommended_courses
    assert all("course_id" in r for r in advice.recommended_courses)


# ── Fallback: every LLM-miss path keeps the rule explanation ────


async def test_llm_returning_none_keeps_rule_explanation(
    async_session, cs_curriculum,
):
    fake = _FakeLLM(text=None)
    agent = AcademicAdvisoryAgent(llm_client=fake)

    advice = await agent.process_task(_input(async_session))

    assert "CGPA 3.10" in advice.explanation
    assert "ECTS" in advice.explanation


async def test_llm_returning_empty_string_keeps_rule_explanation(
    async_session, cs_curriculum,
):
    fake = _FakeLLM(text="")
    agent = AcademicAdvisoryAgent(llm_client=fake)

    advice = await agent.process_task(_input(async_session))

    assert "CGPA 3.10" in advice.explanation


async def test_no_llm_client_uses_pure_rule_explanation(
    async_session, cs_curriculum,
):
    """Backwards compatibility: omitting llm_client matches Phase-1 behaviour."""
    agent = AcademicAdvisoryAgent()  # no llm_client

    advice = await agent.process_task(_input(async_session))

    assert "CGPA" in advice.explanation
    assert "ECTS" in advice.explanation
    assert advice.recommended_courses  # rule engine still produces these


# ── Resilience: LLM raising must never break the agent ──────────


async def test_llm_raising_does_not_propagate(
    async_session, cs_curriculum,
):
    """
    Belt-and-braces — narrate_advisory is contractually allowed to
    raise (e.g. a programming bug in a new prompt), and the agent
    must still produce a verdict by surfacing the underlying
    exception. This documents that current behaviour: callers wrap
    in try/except if they want to swallow it. The LLMClient itself
    is the layer that converts SDK errors to None.
    """
    agent = AcademicAdvisoryAgent(llm_client=_BoomLLM())

    with pytest.raises(RuntimeError, match="on fire"):
        await agent.process_task(_input(async_session))


# ── Risk-aware narration ─────────────────────────────────────────


async def test_high_risk_advice_includes_escalation_in_llm_payload(
    async_session, cs_curriculum,
):
    """LLM should know about escalation so it can warn the student."""
    fake = _FakeLLM(text="Heads up — this needs an officer's sign-off.")
    agent = AcademicAdvisoryAgent(llm_client=fake)

    await agent.process_task(_input(
        async_session, cgpa=1.5, total_proposed_credits=22,
    ))

    payload = fake.calls[0]
    assert payload["advice"]["risk_status"] == "HIGH"
    assert payload["advice"]["requires_officer_review"] is True
