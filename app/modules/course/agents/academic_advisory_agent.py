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

from app.ai.llm_client import LLMClient, LLMUnavailableError
from app.modules.course.agents.course_base_agent import CourseBaseAgent
from app.modules.course.models import Course, CoursePrerequisite
from app.shared.enums import ConsultationMode, RiskStatus


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


# Allowed values for ``ConsultationResult.verdict``. Kept as a literal
# tuple instead of an Enum because Gemini returns the value as a plain
# string and the agent passes it through unchanged.
CONSULTATION_VERDICTS = (
    "ON_TRACK", "AT_RISK", "OFF_TRACK", "NEEDS_REVIEW",
)


@dataclass
class ConsultationResult:
    """
    Output of :meth:`AcademicAdvisoryAgent.consult` — the LLM-backed
    demand-driven advisory verdict the student sees in the portal.

    Fields mirror the Gemini response schema so the service layer can
    persist the payload verbatim:

      verdict              ON_TRACK | AT_RISK | OFF_TRACK | NEEDS_REVIEW
      risk_status          LOW | MEDIUM | HIGH (rule-engine-compatible)
      recommended_courses  list of {course_id?, course_code, title,
                           credit_hours, is_core, reason,
                           requires_override?}
      warnings             list of plain-English actionable concerns
      graduation_impact    {semesters_remaining, on_track,
                           expected_graduation_semester, delay_semesters,
                           critical_path_courses}
      narrative            student-facing summary paragraph
      mode                 PRE_REGISTRATION | REGISTRATION_PLAN | ADD_DROP
      filtered_recommendations
                           course codes the agent dropped from the LLM
                           output because they failed a hard constraint
                           (already passed, or invented). Surfaced for
                           audit so the officer can spot LLM drift.
    """

    verdict: str
    risk_status: RiskStatus
    recommended_courses: list[dict[str, Any]]
    warnings: list[str]
    graduation_impact: dict[str, Any]
    narrative: str
    mode: ConsultationMode
    filtered_recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "risk_status": self.risk_status.value,
            "recommended_courses": list(self.recommended_courses),
            "warnings": list(self.warnings),
            "graduation_impact": dict(self.graduation_impact),
            "narrative": self.narrative,
            "mode": self.mode.value,
            "filtered_recommendations": list(self.filtered_recommendations),
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

    # ── consult (demand-driven LLM consultation) ─────────────────

    async def consult(
        self,
        session: AsyncSession,
        *,
        mode: ConsultationMode,
        student_context: dict[str, Any],
        completed_course_ids: set[uuid.UUID],
        proposed_course_ids: Optional[list[uuid.UUID]] = None,
        add_course_ids: Optional[list[uuid.UUID]] = None,
        drop_course_ids: Optional[list[uuid.UUID]] = None,
        current_term: Optional[dict[str, Any]] = None,
    ) -> ConsultationResult:
        """
        Demand-driven LLM consultation. Used by the three
        ``/advisory/consult/*`` endpoints; the registration submit path
        keeps using :meth:`process_task` (rule engine) so the HIGH-risk
        escalation contract is unchanged.

        The agent builds the full reasoning context (department
        curriculum sequence, prerequisite graph, completed courses,
        registration draft) and asks Gemini for a structured verdict
        that includes a graduation-trajectory analysis. Hard-fail
        contract: if the LLM cannot answer, :class:`LLMUnavailableError`
        bubbles up so the service layer can return 503 rather than
        silently degrading to the rule engine — the consult flow is
        explicitly LLM-first.

        Hard constraints applied to the LLM output before returning:
          - Any recommended course the student has already passed is
            stripped (LLM should never recommend completed work).
          - Any recommended ``course_code`` not present in the
            department curriculum is stripped (catches LLM
            hallucination of invented codes).
          - Stripped codes are surfaced on
            ``ConsultationResult.filtered_recommendations`` for audit.
        """
        if self._llm_client is None:
            raise LLMUnavailableError(
                "GEMINI_API_KEY is not configured; the demand-driven "
                "advisory consult requires an active LLM client."
            )

        department: str = student_context["department"]
        curriculum_rows = (
            await session.execute(
                select(Course).where(
                    Course.department == department,
                    Course.is_deleted == False,  # noqa: E712
                ).order_by(Course.semester.asc(), Course.code.asc())
            )
        ).scalars().all()
        curriculum_by_id = {c.id: c for c in curriculum_rows}
        curriculum_codes = {c.code for c in curriculum_rows}

        # Prerequisite graph: course_id -> [prereq_code, ...]. Only
        # built over the department's curriculum to keep the payload
        # bounded; cross-department prereqs are rare in the AAU
        # undergraduate program.
        prereq_rows = (
            await session.execute(
                select(CoursePrerequisite).where(
                    CoursePrerequisite.course_id.in_(
                        [c.id for c in curriculum_rows]
                    )
                )
            )
        ).scalars().all()
        prereq_codes_by_course: dict[uuid.UUID, list[str]] = {}
        for row in prereq_rows:
            target = curriculum_by_id.get(row.prerequisite_course_id)
            if target is None:
                continue
            prereq_codes_by_course.setdefault(
                row.course_id, []
            ).append(target.code)

        curriculum_payload = [
            {
                "course_code": c.code,
                "title": c.title,
                "credit_hours": c.credit_hours,
                "semester": c.semester,
                "prerequisite_codes": sorted(
                    prereq_codes_by_course.get(c.id, [])
                ),
            }
            for c in curriculum_rows
        ]
        completed_codes = sorted(
            curriculum_by_id[cid].code
            for cid in completed_course_ids
            if cid in curriculum_by_id
        )

        def _codes_for(ids: Optional[list[uuid.UUID]]) -> list[str]:
            if not ids:
                return []
            return sorted(
                curriculum_by_id[cid].code
                for cid in ids
                if cid in curriculum_by_id
            )

        consultation_payload: dict[str, Any] = {
            "mode": mode.value,
            "student": {
                "id": student_context.get("student_id"),
                "current_semester": student_context["current_semester"],
                "cgpa": student_context.get("cgpa"),
                "sponsorship_type": student_context.get("sponsorship_type"),
            },
            "department": {
                "name": department,
                "curriculum": curriculum_payload,
            },
            "history": {
                "completed_course_codes": completed_codes,
                "completed_count": len(completed_codes),
            },
            "current_term": current_term or {},
            "draft": {
                "proposed_course_codes": _codes_for(proposed_course_ids),
            },
            "proposed_changes": {
                "add_course_codes": _codes_for(add_course_ids),
                "drop_course_codes": _codes_for(drop_course_ids),
            },
        }

        raw = await self._llm_client.consult_academic_plan(
            consultation_payload
        )
        return self._postprocess_consultation(
            raw=raw,
            mode=mode,
            curriculum_codes=curriculum_codes,
            completed_codes=set(completed_codes),
        )

    def _postprocess_consultation(
        self,
        *,
        raw: dict[str, Any],
        mode: ConsultationMode,
        curriculum_codes: set[str],
        completed_codes: set[str],
    ) -> ConsultationResult:
        """
        Apply the agent's hard constraints to the LLM's structured
        output. The LLM owns the reasoning, but the agent owns the
        final shape — anything the LLM tries to recommend that
        violates a constraint is dropped and surfaced on
        ``filtered_recommendations`` so the audit trail is honest
        about what the LLM said vs. what the student saw.
        """
        verdict = raw.get("verdict", "NEEDS_REVIEW")
        if verdict not in CONSULTATION_VERDICTS:
            verdict = "NEEDS_REVIEW"

        risk_raw = raw.get("risk_status", "MEDIUM")
        try:
            risk = RiskStatus(risk_raw)
        except ValueError:
            risk = RiskStatus.MEDIUM

        kept: list[dict[str, Any]] = []
        filtered: list[str] = []
        for rec in raw.get("recommended_courses") or []:
            if not isinstance(rec, dict):
                continue
            code = rec.get("course_code")
            if not code:
                continue
            if code in completed_codes:
                filtered.append(f"{code} (already completed)")
                continue
            if code not in curriculum_codes:
                filtered.append(f"{code} (not in department curriculum)")
                continue
            kept.append(rec)

        warnings = [
            str(w) for w in (raw.get("warnings") or [])
            if isinstance(w, (str, int, float))
        ]
        if filtered:
            warnings.append(
                "Some LLM-suggested courses were dropped because they "
                "failed a hard validation rule. See "
                "filtered_recommendations for details."
            )

        graduation_impact = raw.get("graduation_impact") or {}
        if not isinstance(graduation_impact, dict):
            graduation_impact = {}

        narrative = str(raw.get("narrative") or "").strip()
        if not narrative:
            narrative = (
                "The advisor agent returned a verdict of "
                f"{verdict} but did not include a narrative summary."
            )

        return ConsultationResult(
            verdict=verdict,
            risk_status=risk,
            recommended_courses=kept,
            warnings=warnings,
            graduation_impact=graduation_impact,
            narrative=narrative,
            mode=mode,
            filtered_recommendations=filtered,
        )
