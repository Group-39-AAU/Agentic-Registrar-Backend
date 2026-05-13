"""
Academic Advisory Agent.

Realises SDS Tables 67–69:

    Class AcademicAdvisoryAgent
        - advisorRules: RuleSet
        - suggestionEngine: AIModel

        + evaluateStudyPlan(studentID): void
        + provideAcademicGuidance(studentID): Advice
        + approveCourseLoad(studentID): Boolean
        + flagRiskLevel(studentID): RiskStatus

Rule engine is the source of truth for risk + recommendations; an
optional :class:`~app.ai.llm_client.LLMClient` is asked to rewrite the
explanation paragraph in plain English. When the LLM is unavailable or
errors out, the rule-based explanation is used unchanged. The CGPA
thresholds and "heavy load" cut-off are class-level constants so tests
can override them without touching the singleton.

Hard rules (SDS Table 68 invariants):
  - The suggestion engine must prioritise mandatory core courses
    over electives.
  - Approve-course-load must escalate HIGH-risk verdicts to the
    CourseManagementOfficer rather than silently auto-approving.
  - LLM output is strictly additive: the structured verdict
    (risk_status, recommended_courses, requires_officer_review)
    must never be derived from the LLM.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm_client import LLMClient
from app.modules.course.agents.course_base_agent import CourseBaseAgent
from app.modules.course.models import Course
from app.shared.enums import RiskStatus


# ── Result containers ────────────────────────────────────────────


@dataclass
class GapAnalysis:
    """Output of :meth:`evaluate_study_plan` (SDS Table 69)."""

    department: str
    current_semester: int
    completed_course_ids: list[str] = field(default_factory=list)
    completed_course_codes: list[str] = field(default_factory=list)
    remaining_required_course_ids: list[str] = field(default_factory=list)
    remaining_required_course_codes: list[str] = field(default_factory=list)
    completed_count: int = 0
    remaining_count: int = 0
    curriculum_size: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Advice:
    """Aggregated advisory verdict returned by :meth:`process_task`."""

    risk_status: RiskStatus
    recommended_courses: list[dict[str, Any]]
    gap_analysis: GapAnalysis
    explanation: str
    requires_officer_review: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_status": self.risk_status.value,
            "recommended_courses": list(self.recommended_courses),
            "gap_analysis": self.gap_analysis.to_dict(),
            "explanation": self.explanation,
            "requires_officer_review": self.requires_officer_review,
        }


# ── Agent ────────────────────────────────────────────────────────


class AcademicAdvisoryAgent(CourseBaseAgent):
    """SDS §3.1.3 + §5.3 Tables 67–69."""

    AGENT_ID_PREFIX = "AGENT_AAA_"

    # SDS Table 74-aligned thresholds (Warning < 2.0, Distinction > 3.5).
    # The advisory agent reuses the same Warning floor for HIGH risk.
    HIGH_RISK_CGPA_BELOW: float = 2.0
    MEDIUM_RISK_CGPA_BELOW: float = 2.75
    HIGH_LOAD_THRESHOLD_ECTS: int = 18

    def __init__(
        self,
        agent_id: Optional[str] = None,
        *,
        high_risk_cgpa: Optional[float] = None,
        medium_risk_cgpa: Optional[float] = None,
        high_load_threshold: Optional[int] = None,
        llm_client: Optional[LLMClient] = None,
    ) -> None:
        super().__init__(agent_id=agent_id or f"{self.AGENT_ID_PREFIX}DEFAULT")
        self._high_risk_cgpa = (
            high_risk_cgpa if high_risk_cgpa is not None
            else self.HIGH_RISK_CGPA_BELOW
        )
        self._medium_risk_cgpa = (
            medium_risk_cgpa if medium_risk_cgpa is not None
            else self.MEDIUM_RISK_CGPA_BELOW
        )
        self._high_load_threshold = (
            high_load_threshold if high_load_threshold is not None
            else self.HIGH_LOAD_THRESHOLD_ECTS
        )
        # Optional. None ⇒ the rule-based explanation is final.
        self._llm_client = llm_client

    # ── flag_risk_level (SDS Table 69) ───────────────────────────

    def flag_risk_level(
        self, cgpa: float, total_credits: int,
    ) -> RiskStatus:
        """
        Pure function. Rule-based classifier:

            HIGH   - CGPA < high_risk_cgpa, OR
                     (CGPA < medium_risk_cgpa AND load >= high_load_threshold)
            MEDIUM - CGPA < medium_risk_cgpa, OR
                     load >= high_load_threshold
            LOW    - otherwise
        """
        if cgpa < self._high_risk_cgpa:
            return RiskStatus.HIGH
        if (
            cgpa < self._medium_risk_cgpa
            and total_credits >= self._high_load_threshold
        ):
            return RiskStatus.HIGH
        if cgpa < self._medium_risk_cgpa:
            return RiskStatus.MEDIUM
        if total_credits >= self._high_load_threshold:
            return RiskStatus.MEDIUM
        return RiskStatus.LOW

    # ── evaluate_study_plan (SDS Table 69) ───────────────────────

    async def evaluate_study_plan(
        self,
        session: AsyncSession,
        department: str,
        current_semester: int,
        completed_course_ids: set[uuid.UUID],
    ) -> GapAnalysis:
        """
        Identify completed-vs-remaining courses against the student's
        curriculum. Phase-1 heuristic: a course is "required so far"
        if it belongs to the student's department and its
        ``semester`` is ``<= current_semester``.
        """
        rows = (
            await session.execute(
                select(Course).where(
                    Course.department == department,
                    Course.semester <= current_semester,
                    Course.is_deleted == False,  # noqa: E712
                )
            )
        ).scalars().all()

        completed = [c for c in rows if c.id in completed_course_ids]
        remaining = [c for c in rows if c.id not in completed_course_ids]

        return GapAnalysis(
            department=department,
            current_semester=current_semester,
            completed_course_ids=[str(c.id) for c in completed],
            completed_course_codes=sorted(c.code for c in completed),
            remaining_required_course_ids=[str(c.id) for c in remaining],
            remaining_required_course_codes=sorted(c.code for c in remaining),
            completed_count=len(completed),
            remaining_count=len(remaining),
            curriculum_size=len(rows),
        )

    # ── provide_academic_guidance (SDS Table 69) ─────────────────

    async def provide_academic_guidance(
        self,
        session: AsyncSession,
        department: str,
        current_semester: int,
        completed_course_ids: set[uuid.UUID],
    ) -> list[dict[str, Any]]:
        """
        Prioritised list of recommended next courses. SDS Table 68
        invariant: mandatory core courses precede electives. Phase-1
        simplification flags every course as ``is_core=True`` since
        the catalog does not yet distinguish core from elective; the
        ordering is by semester then code so the output is stable.
        """
        rows = (
            await session.execute(
                select(Course).where(
                    Course.department == department,
                    # Look at current semester and the next one.
                    Course.semester <= current_semester + 1,
                    Course.is_deleted == False,  # noqa: E712
                ).order_by(Course.semester.asc(), Course.code.asc())
            )
        ).scalars().all()

        recommendations: list[dict[str, Any]] = []
        for course in rows:
            if course.id in completed_course_ids:
                continue
            recommendations.append({
                "course_id": str(course.id),
                "code": course.code,
                "title": course.title,
                "credit_hours": course.credit_hours,
                "semester": course.semester,
                "is_core": True,
                "reason": (
                    f"Required for semester {course.semester} "
                    f"of the {department} program."
                ),
            })
        return recommendations

    # ── approve_course_load (SDS Table 69) ───────────────────────

    def approve_course_load(self, risk_status: RiskStatus) -> bool:
        """
        Return True iff the proposed load is approvable without
        officer escalation. Phase-1 rule: HIGH risk always escalates.
        """
        return risk_status != RiskStatus.HIGH

    # ── BaseAgent: process_task aggregates the three checks ──────

    async def process_task(
        self, input_data: dict[str, Any],
    ) -> Advice:
        """
        Run the full advisory pipeline.

        Required keys in ``input_data``:
            session: AsyncSession
            department: str
            current_semester: int
            cgpa: float
            total_proposed_credits: int
            completed_course_ids: set[uuid.UUID]

        Returns an :class:`Advice` aggregate. The service layer
        persists it as an ``AdvisoryRecommendation`` row and surfaces
        ``requires_officer_review`` as the escalation flag.
        """
        session: AsyncSession = input_data["session"]
        department: str = input_data["department"]
        current_semester: int = input_data["current_semester"]
        cgpa: float = input_data["cgpa"]
        total_credits: int = input_data["total_proposed_credits"]
        completed: set[uuid.UUID] = input_data.get(
            "completed_course_ids", set(),
        )

        gap = await self.evaluate_study_plan(
            session, department, current_semester, completed,
        )
        recs = await self.provide_academic_guidance(
            session, department, current_semester, completed,
        )
        risk = self.flag_risk_level(cgpa, total_credits)
        approvable = self.approve_course_load(risk)

        explanation_parts = [
            f"CGPA {cgpa:.2f} with proposed load {total_credits} ECTS.",
            f"Curriculum gap: {gap.remaining_count} of "
            f"{gap.curriculum_size} required courses still outstanding.",
        ]
        if risk == RiskStatus.HIGH:
            explanation_parts.append(
                "HIGH risk — escalated for officer review."
            )
        elif risk == RiskStatus.MEDIUM:
            explanation_parts.append(
                "MEDIUM risk — student should consider a lighter load "
                "or seek manual advisor input."
            )
        else:
            explanation_parts.append("LOW risk — load is approvable.")

        rule_explanation = " ".join(explanation_parts)

        # LLM enrichment is strictly additive: rule engine owns the
        # verdict (risk_status, recommendations, escalation flag); the
        # LLM only rewrites the prose. ``narrate_advisory`` returns
        # None on any SDK failure, so the rule explanation survives.
        explanation = await self._maybe_narrate(
            rule_explanation=rule_explanation,
            risk=risk,
            recs=recs,
            gap=gap,
            cgpa=cgpa,
            total_credits=total_credits,
            department=department,
            current_semester=current_semester,
            requires_officer_review=not approvable,
        )

        return Advice(
            risk_status=risk,
            recommended_courses=recs,
            gap_analysis=gap,
            explanation=explanation,
            requires_officer_review=not approvable,
        )

    async def _maybe_narrate(
        self,
        *,
        rule_explanation: str,
        risk: RiskStatus,
        recs: list[dict[str, Any]],
        gap: GapAnalysis,
        cgpa: float,
        total_credits: int,
        department: str,
        current_semester: int,
        requires_officer_review: bool,
    ) -> str:
        """Ask the LLM for a friendlier paragraph; fall back on miss."""
        if self._llm_client is None:
            return rule_explanation

        structured_advice = {
            "risk_status": risk.value,
            "recommended_courses": recs,
            "gap_analysis": gap.to_dict(),
            "rule_explanation": rule_explanation,
            "requires_officer_review": requires_officer_review,
        }
        student_context = {
            "department": department,
            "current_semester": current_semester,
            "cgpa": cgpa,
            "proposed_credits": total_credits,
        }
        narrative = await self._llm_client.narrate_advisory(
            structured_advice, student_context,
        )
        return narrative or rule_explanation
