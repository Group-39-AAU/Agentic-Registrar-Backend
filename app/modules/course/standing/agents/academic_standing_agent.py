"""
AcademicStandingAgent — Track C's rule-based standing evaluator.

Architecture (different from the Track-B grading agent on purpose):

  - The Article-91 rules in :mod:`app.modules.course.standing.rules`
    are mechanical thresholds; the AGENT is the deterministic
    decision-maker. The LLM, when wired, plays only a narrative
    role (turning the rule trail into a student-facing paragraph),
    NOT a reasoner role.

  - This follows the existing rule-based-with-LLM-narrative pattern
    used by :class:`EnrollmentAdjustmentAgent` and
    :class:`AcademicAdvisoryAgent`. Memory
    feedback_grading_agent_design.md explicitly carves out Track B's
    grading agent as the LLM-as-reasoner exception; Track C reverts
    to the standard pattern because dismissal thresholds are
    mechanical, not judgement calls.

PR C2 wires the LLM narration as an optional best-effort hook;
when unavailable it falls back to the rules-engine reason chain.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from app.ai.llm_client import LLMClient
from app.modules.course.agents.course_base_agent import CourseBaseAgent
from app.modules.course.standing.rules import (
    StandingComputeInput, StandingVerdict, evaluate_status,
)
from app.shared.enums import AcademicStatusType, AgentStatus


logger = logging.getLogger(__name__)


@dataclass
class StandingProposal:
    """
    What the agent returns after evaluating one (student, term) pair.

    The service layer persists the proposal into an
    :class:`AcademicStanding` row and records a ``PROPOSED`` event in
    the history table. The DH's authorise / override action later
    sets ``final_status`` and writes the audit row.
    """
    proposed_status: AcademicStatusType
    requires_review: bool
    reasons: list[str]
    rule_citations: list[str]
    narrative: Optional[str] = None
    agent_id: str = ""
    # Inputs echoed back so the service can persist them on the
    # AcademicStanding row without re-deriving them.
    input_snapshot: dict[str, Any] = field(default_factory=dict)

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "proposed_status": self.proposed_status.value,
            "requires_review": self.requires_review,
            "reasons": list(self.reasons),
            "rule_citations": list(self.rule_citations),
            "narrative": self.narrative,
            "agent_id": self.agent_id,
        }


class AcademicStandingAgent(CourseBaseAgent):
    """
    Rule-based standing evaluator. ``llm_client`` is an optional
    narrative-only collaborator — if provided AND the verdict is
    consequential (DISMISSED or WARNING), the agent asks it to turn
    the rule chain into a plain-language paragraph the student sees
    on authorise. Failures degrade silently to the rule-trail
    summary; the verdict itself never depends on the LLM.
    """

    AGENT_ID_PREFIX = "AGENT_ASA_"

    # Statuses for which we bother calling the LLM. PROMOTED rarely
    # needs an explanation; INCOMPLETE is officer-facing context
    # rather than a student-facing narrative.
    _NARRATIVE_STATUSES: frozenset[AcademicStatusType] = frozenset({
        AcademicStatusType.WARNING,
        AcademicStatusType.DISMISSED,
    })

    def __init__(
        self,
        agent_id: Optional[str] = None,
        *,
        llm_client: Optional[LLMClient] = None,
    ) -> None:
        super().__init__(agent_id=agent_id or f"{self.AGENT_ID_PREFIX}DEFAULT")
        self._llm_client = llm_client

    async def process_task(
        self, input_data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        BaseAgent hook so the agent can be invoked through the generic
        registry. Real callers should use :meth:`propose_standing`
        directly so they can pass an ergonomic
        :class:`StandingComputeInput` instead of a dict.
        """
        ctx = input_data.get("context")
        if not isinstance(ctx, StandingComputeInput):
            raise ValueError(
                "AcademicStandingAgent.process_task requires "
                "input_data['context'] to be a StandingComputeInput."
            )
        proposal = await self.propose_standing(ctx)
        return proposal.to_audit_dict()

    async def propose_standing(
        self,
        ctx: StandingComputeInput,
        *,
        student_context: Optional[dict[str, Any]] = None,
    ) -> StandingProposal:
        """
        Run the Article-91 rules engine and (optionally) enrich with
        an LLM-generated narrative. Always deterministic w.r.t. the
        verdict — the LLM only ever rewrites the reason string.

        ``student_context`` is a free-form dict (full_name, student_id,
        prior CGPA, etc.) that's only forwarded to the LLM. Not used
        in any rule evaluation.
        """
        self.status = AgentStatus.BUSY
        try:
            verdict = evaluate_status(ctx)
            narrative = await self._maybe_narrate(
                verdict=verdict, ctx=ctx, student_context=student_context,
            )
            self.status = AgentStatus.IDLE
            return StandingProposal(
                proposed_status=verdict.proposed_status,
                requires_review=verdict.requires_review,
                reasons=list(verdict.reasons),
                rule_citations=list(verdict.rule_citations),
                narrative=narrative or " ".join(verdict.reasons),
                agent_id=self.agent_id,
                input_snapshot={
                    "sgpa": ctx.sgpa,
                    "cgpa": ctx.cgpa,
                    "term_credit_hours": ctx.term_credit_hours,
                    "f_count": ctx.f_count,
                    "f_credit_total": ctx.f_credit_total,
                    "is_first_semester": ctx.is_first_semester,
                    "is_first_year": ctx.is_first_year,
                    "prior_status": (
                        ctx.prior_status.value
                        if ctx.prior_status else None
                    ),
                    "consecutive_warning_count": ctx.consecutive_warning_count,
                },
            )
        except Exception:
            self.status = AgentStatus.ERROR
            raise

    async def _maybe_narrate(
        self,
        *,
        verdict: StandingVerdict,
        ctx: StandingComputeInput,
        student_context: Optional[dict[str, Any]],
    ) -> Optional[str]:
        """
        Best-effort LLM narration when the verdict is consequential
        and an LLM client is available. Returns ``None`` to fall back
        to the rules-engine reason chain.

        Currently a stub returning ``None``: the LLMClient does not
        yet expose a ``narrate_standing`` method, and PR C2 ships the
        rules-only path on purpose ("workable but not complex"). A
        later hardening PR can wire the actual call.
        """
        if self._llm_client is None:
            return None
        if verdict.proposed_status not in self._NARRATIVE_STATUSES:
            return None
        narrate_fn = getattr(self._llm_client, "narrate_standing", None)
        if narrate_fn is None:
            return None
        try:
            return await narrate_fn(
                verdict={
                    "status": verdict.proposed_status.value,
                    "reasons": list(verdict.reasons),
                    "rule_citations": list(verdict.rule_citations),
                },
                student_context=student_context or {},
                term_context={
                    "sgpa": ctx.sgpa, "cgpa": ctx.cgpa,
                    "f_count": ctx.f_count,
                },
            )
        except Exception as exc:  # network / SDK / etc — degrade silently
            logger.warning(
                "standing_llm_narration_failed",
                extra={
                    "agent_id": self.agent_id,
                    "error_type": type(exc).__name__,
                },
            )
            return None
