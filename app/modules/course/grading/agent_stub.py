"""
Placeholder Grading-Monitor agent — PR 2 shim.

PR 3 replaces this module with the real LLM-as-reasoner
``GradingMonitorAgent`` (tools compile deterministic context; LLM
decides APPROVE vs FLAG as a department head). For PR 2 we ship a
trivial verdict so the submit endpoint can be exercised end-to-end:
the agent always APPROVES, no flags, no reasoning.

Persisting agent runs into ``grade_agent_reviews`` is deferred to
PR 3 alongside the real agent — for PR 2 the result is returned in
the submit response only.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class StubAgentVerdict:
    """
    Shape of the agent's reply. Mirrors the dataclass the real agent
    will return in PR 3 so the service-layer wiring doesn't change
    between PRs — only the agent module is swapped.
    """
    verdict: Literal["APPROVE", "FLAG"]
    flags: list[str] = field(default_factory=list)
    reasoning: str = ""


async def review_batch_stub(*, batch_id: uuid.UUID) -> StubAgentVerdict:
    """Stand-in verdict generator. Always approves until PR 3 ships."""
    del batch_id  # No-op placeholder; PR 3 reads the batch and runs tools.
    return StubAgentVerdict(
        verdict="APPROVE",
        flags=[],
        reasoning=(
            "PR 2 stub agent — no checks performed. The real "
            "GradingMonitorAgent lands in PR 3."
        ),
    )
