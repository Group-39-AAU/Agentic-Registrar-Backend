"""
GradingMonitorAgent — Track B "department-head substitute".

This is the **LLM-as-reasoner** agent the user explicitly asked for
(memory: feedback_grading_agent_design). Architecture:

  - Tools in ``../tools/`` compile deterministic statistical context
    (class stats, distribution, outlier candidates, identical-score
    clusters, missing-cell check, breakdown integrity, roster
    composition, historical baseline, deadline status). Tools never
    decide; they provide evidence.
  - The LLM is ALWAYS invoked. It receives the full context dict
    plus the instructor's justification (if any) and reasons as a
    department head. It returns ``{verdict, flags[], reasoning}``.
  - The verdict comes FROM the LLM, not from local rule thresholds.
    If the LLM is unavailable, the agent records a PENDING review
    rather than substituting a rule-based answer — the DH must
    re-trigger manually (PR 4 endpoint).

Persistence: every agent run writes one row to
``grade_agent_reviews`` (append-only). The latest row is what the
instructor sees; the DH sees the full iteration history.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm_client import LLMClient, LLMUnavailableError
from app.modules.course.agents.course_base_agent import CourseBaseAgent
from app.modules.course.grading.models import (
    AssessmentBreakdown, AssessmentComponent, GradeAgentReview, GradeBatch,
    StudentComponentScore,
)
from app.modules.course.grading.roster import derive_section_course_roster
from app.modules.course.grading.letter_scale import letter_for_numeric
from app.modules.course.grading.tools import (
    check_breakdown_integrity, check_deadline_status,
    compare_to_section_history, compute_class_statistics,
    compute_component_statistics, compute_letter_distribution,
    find_identical_score_clusters, list_missing_entries,
    list_score_outliers, summarize_roster,
)
from app.modules.course.models import (
    AcademicTerm, Course, Section,
)
from app.shared.enums import AgentStatus


@dataclass
class GradingReview:
    """
    Aggregated agent verdict. The service layer persists this verbatim
    into a ``GradeAgentReview`` row and uses ``verdict`` to drive the
    batch's status transition (APPROVE → SUBMITTED, FLAG → FLAGGED,
    PENDING → leave at SUBMITTED with a manual-re-trigger note).
    """
    verdict: str           # "APPROVE" | "FLAG" | "PENDING"
    flags: list[dict[str, Any]]
    reasoning: str
    tool_findings: dict[str, Any]
    agent_id: str

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "flags": list(self.flags),
            "reasoning": self.reasoning,
            "tool_findings": dict(self.tool_findings),
            "agent_id": self.agent_id,
        }


class GradingMonitorAgent(CourseBaseAgent):
    """
    SDS §3.1.3 + Track B checklist (lines 147–157): Assessment
    Validation / Grading Monitoring agent.

    Constructor accepts an optional :class:`LLMClient`; production
    wiring injects the default client built from settings. Tests
    inject a fake client that returns a scripted dict so the agent
    is exercisable without a network call.
    """

    AGENT_ID_PREFIX = "AGENT_GMA_"

    def __init__(
        self,
        agent_id: Optional[str] = None,
        *,
        llm_client: Optional[LLMClient] = None,
        today: Optional[date] = None,
    ) -> None:
        super().__init__(agent_id=agent_id or f"{self.AGENT_ID_PREFIX}DEFAULT")
        self._llm_client = llm_client
        # ``today`` is injectable so deadline-status tests are
        # deterministic without monkey-patching ``date.today``.
        self._today_override = today

    # ── Entry point ─────────────────────────────────────────────

    async def process_task(
        self, input_data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Entry point per :class:`BaseAgent` contract.

        ``input_data`` must contain:
            ``db``: AsyncSession
            ``batch_id``: UUID

        Returns the agent's verdict dict so callers (the grading
        service) can drive their next step.
        """
        db: AsyncSession = input_data["db"]
        batch_id: uuid.UUID = input_data["batch_id"]
        review = await self.review_batch(db=db, batch_id=batch_id)
        return review.to_audit_dict()

    # ── Public method the service calls ─────────────────────────

    async def review_batch(
        self,
        *,
        db: AsyncSession,
        batch_id: uuid.UUID,
    ) -> GradingReview:
        """
        Compile deterministic context, call the LLM, return a
        :class:`GradingReview`. Caller persists it and uses the
        verdict to transition the batch.
        """
        self.set_status(AgentStatus.BUSY)
        try:
            context = await self._compile_context(db=db, batch_id=batch_id)
        except Exception:
            self.set_status(AgentStatus.ERROR)
            raise

        # Always call the LLM — no rule-based shortcut.
        if self._llm_client is None:
            self.set_status(AgentStatus.WAITING_HUMAN)
            return GradingReview(
                verdict="PENDING",
                flags=[],
                reasoning="",
                tool_findings=context,
                agent_id=self.agent_id,
            )

        try:
            llm_out = await self._llm_client.review_grade_batch_as_dh(
                review_payload=context,
            )
        except LLMUnavailableError:
            self.set_status(AgentStatus.WAITING_HUMAN)
            return GradingReview(
                verdict="PENDING",
                flags=[],
                reasoning="",
                tool_findings=context,
                agent_id=self.agent_id,
            )

        verdict = (llm_out.get("verdict") or "").upper()
        if verdict not in {"APPROVE", "FLAG"}:
            # Malformed LLM output → treat as PENDING so a human reviews.
            self.set_status(AgentStatus.WAITING_HUMAN)
            return GradingReview(
                verdict="PENDING",
                flags=[],
                reasoning=(
                    "LLM returned an invalid verdict; please re-run "
                    "the agent or escalate to a human reviewer."
                ),
                tool_findings=context,
                agent_id=self.agent_id,
            )

        self.set_status(AgentStatus.IDLE)
        return GradingReview(
            verdict=verdict,
            flags=list(llm_out.get("flags") or []),
            reasoning=str(llm_out.get("reasoning") or ""),
            tool_findings=context,
            agent_id=self.agent_id,
        )

    # ── Context compiler (tool orchestration) ───────────────────

    async def _compile_context(
        self,
        *,
        db: AsyncSession,
        batch_id: uuid.UUID,
    ) -> dict[str, Any]:
        """
        Run every tool against the batch and merge the outputs into
        a single JSON-serialisable dict the LLM consumes verbatim.
        """
        batch = (
            await db.execute(
                select(GradeBatch).where(GradeBatch.id == batch_id)
            )
        ).scalar_one()

        # Fixed-shape entity reads — small queries, parallel-safe but
        # SQLAlchemy's async session is single-threaded so we do them
        # sequentially for simplicity.
        section = (
            await db.execute(
                select(Section).where(Section.id == batch.section_id)
            )
        ).scalar_one()
        course = (
            await db.execute(
                select(Course).where(Course.id == batch.course_id)
            )
        ).scalar_one()
        term = (
            await db.execute(
                select(AcademicTerm).where(AcademicTerm.id == batch.term_id)
            )
        ).scalar_one()
        breakdown = (
            await db.execute(
                select(AssessmentBreakdown).where(
                    AssessmentBreakdown.id == batch.breakdown_id,
                )
            )
        ).scalar_one()
        components = sorted(
            (await db.execute(
                select(AssessmentComponent).where(
                    AssessmentComponent.breakdown_id == breakdown.id,
                )
            )).scalars().all(),
            key=lambda c: c.order_index,
        )

        roster = await derive_section_course_roster(
            db, section_id=batch.section_id, course_id=batch.course_id,
        )
        scores = (await db.execute(
            select(StudentComponentScore).where(
                StudentComponentScore.batch_id == batch.id,
            )
        )).scalars().all()
        score_by_cell: dict[tuple[uuid.UUID, uuid.UUID], float | None] = {
            (s.student_id, s.component_id): s.score for s in scores
        }

        # Hydrate per-student numeric + letter for the stats tools.
        per_student: list[dict[str, Any]] = []
        component_raw: dict[str, list[float]] = {c.name: [] for c in components}
        for member in roster:
            weighted = 0.0
            complete = True
            for comp in components:
                raw = score_by_cell.get((member.student_id, comp.id))
                if raw is None:
                    complete = False
                    continue
                component_raw[comp.name].append(raw)
                weighted += (raw / comp.max_score) * comp.weight
            if not complete:
                # Skip incomplete rows — the missing-entries tool
                # surfaces them separately.
                continue
            weighted = max(0.0, min(100.0, weighted))
            per_student.append({
                "student_number": member.student_number,
                "full_name": member.full_name,
                "is_added_via_drop": member.is_added_via_drop,
                "numeric_score": round(weighted, 4),
                "letter_grade": letter_for_numeric(weighted).value,
            })

        # Roster summary (for the small-class heuristic).
        original_count = sum(1 for m in roster if not m.is_added_via_drop)
        added_count = len(roster) - original_count

        # Tool calls.
        class_stats = compute_class_statistics(
            [s["numeric_score"] for s in per_student],
        )
        distribution = compute_letter_distribution([
            letter_for_numeric(s["numeric_score"]) for s in per_student
        ])
        component_stats = compute_component_statistics([
            {
                "name": c.name, "weight": c.weight, "max_score": c.max_score,
                "raw_scores": component_raw[c.name],
            }
            for c in components
        ])
        outliers = list_score_outliers(per_student)
        clusters = find_identical_score_clusters(
            [s["numeric_score"] for s in per_student],
        )
        # Build the (student × component) score map for the
        # missing-entries tool, keyed by string ids.
        score_map: dict[tuple[str, str], float | None] = {}
        for member in roster:
            for comp in components:
                score_map[(str(member.student_id), comp.name)] = (
                    score_by_cell.get((member.student_id, comp.id))
                )
        missing = list_missing_entries(
            roster_student_ids=[str(m.student_id) for m in roster],
            component_names=[c.name for c in components],
            score_map=score_map,
        )
        breakdown_integrity = check_breakdown_integrity([
            {"name": c.name, "weight": c.weight, "max_score": c.max_score}
            for c in components
        ])
        roster_summary = summarize_roster(
            total=len(roster),
            original_count=original_count,
            added_count=added_count,
        )
        history = await compare_to_section_history(
            db, course_id=batch.course_id, current_term_id=batch.term_id,
        )

        today = self._today_override or datetime.now(timezone.utc).date()
        submitted_at = batch.submitted_at or datetime.now(timezone.utc)
        deadline = check_deadline_status(
            submitted_at=submitted_at,
            term_end_date=term.end_date,
            today=today,
        )

        # Final context dict — what the LLM literally sees.
        return {
            "course": {
                "code": course.code,
                "title": course.title,
                "credit_hours": course.credit_hours,
                "semester": course.semester,
                "department": course.department,
            },
            "section": {
                "section_code": section.section_code,
                "term_name": term.term_name,
                "term_phase": term.phase.value,
            },
            "breakdown": breakdown_integrity,
            "roster": roster_summary,
            "class_stats": class_stats,
            "distribution": distribution,
            "components": component_stats,
            "outliers": outliers,
            "identical_clusters": clusters,
            "missing": missing,
            "history": history,
            "deadline": deadline,
            "iteration": batch.iteration_count,
            "instructor_justification": batch.instructor_justification,
            "per_student": per_student,
        }
