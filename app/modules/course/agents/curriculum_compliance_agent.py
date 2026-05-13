"""
Curriculum Compliance Agent.

Realises SDS Tables 64–66:

    Class CurriculumComplianceAgent
        - curriculumInformation: Map
        - validationRules: RuleSet

        + verifyPrerequisites(studentID, courseID): Boolean
        + validateRegistration(app: Registration): Boolean
        + checkPaymentStatus(studentID): Boolean

The agent is rule-based in Phase 1: there is no LLM call, and the
curriculum information is read directly from the
:class:`CoursePrerequisite` table. Phase 2 will route policy
questions through the pgvector knowledge base via the inherited
``ground_in_policy`` hook.

Each public check returns a :class:`ComplianceCheckResult` rather
than a bare bool so the service layer can surface plain-language
reasons to the student and write structured details to the audit
log without having to reconstruct them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.course.agents.course_base_agent import CourseBaseAgent
from app.modules.course.models import (
    Course, CoursePrerequisite, Registration,
)
from app.modules.course.services import PayMock, pay_mock


# Hard invariants from SDS Tables 65, 80 — names match the SDS
# verbatim so a reader of the design doc can grep the codebase.
MAX_CREDIT_LOAD_ECTS = 22


@dataclass
class ComplianceCheckResult:
    """
    Structured outcome of a single compliance check. ``passed`` is
    the boolean used by the surrounding state machine; ``reasons``
    feeds back into the student-facing portal; ``details`` is dropped
    into the audit-log metadata payload.
    """

    passed: bool
    reasons: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


class CurriculumComplianceAgent(CourseBaseAgent):
    """
    Validates a registration against three rule sets:

      1. Prerequisites — every chosen course's prereq graph must
         resolve against the student's completed-course set.
      2. Credit load — total ECTS must not exceed 22 (SDS Table 65).
      3. Payment    — every chosen course must be marked paid in
         PayMock.

    The agent is intentionally stateless across calls: each method
    accepts everything it needs as a parameter so the agent is
    trivially testable without a session-bound graph.
    """

    AGENT_ID_PREFIX = "AGENT_CCA_"

    def __init__(
        self,
        agent_id: Optional[str] = None,
        *,
        payment_service: Optional[PayMock] = None,
        max_credit_load: int = MAX_CREDIT_LOAD_ECTS,
    ) -> None:
        super().__init__(agent_id=agent_id or f"{self.AGENT_ID_PREFIX}DEFAULT")
        # Explicit `is None` rather than `or` — a caller-supplied PayMock
        # could legitimately be empty, and `or` would silently fall back
        # to the module singleton, breaking test isolation.
        self._pay = payment_service if payment_service is not None else pay_mock
        self._max_credit_load = max_credit_load

    # ── verify_prerequisites (SDS Table 66) ──────────────────────

    async def verify_prerequisites(
        self,
        session: AsyncSession,
        student_id: uuid.UUID,
        course_id: uuid.UUID,
        completed_course_ids: set[uuid.UUID],
        overridden_course_ids: Optional[set[uuid.UUID]] = None,
    ) -> ComplianceCheckResult:
        """
        Returns ``passed=True`` iff every CoursePrerequisite of
        ``course_id`` is in ``completed_course_ids``. On failure the
        ``reasons`` list contains one line per missing prerequisite,
        keyed by course code for human readability.

        ``overridden_course_ids`` carries the set of courses for which
        a Department Head has granted a prerequisite bypass per SRS
        §3.5. If ``course_id`` is in that set, this check returns
        passed=True with a flag in ``details`` so the audit log knows
        the verdict came from an override rather than a clean pass.

        The ``student_id`` parameter is here so the audit log can
        attribute the check to a student even though the underlying
        rule does not branch on student identity.
        """
        del student_id  # used only by audit-log callers

        if overridden_course_ids and course_id in overridden_course_ids:
            return ComplianceCheckResult(
                passed=True,
                details={
                    "course_id": str(course_id),
                    "via_prerequisite_override": True,
                },
            )

        prereqs = (
            await session.execute(
                select(CoursePrerequisite).where(
                    CoursePrerequisite.course_id == course_id
                )
            )
        ).scalars().all()

        missing = [
            p for p in prereqs
            if p.prerequisite_course_id not in completed_course_ids
        ]
        if not missing:
            return ComplianceCheckResult(passed=True)

        missing_courses = (
            await session.execute(
                select(Course).where(
                    Course.id.in_([p.prerequisite_course_id for p in missing])
                )
            )
        ).scalars().all()
        missing_codes = sorted([c.code for c in missing_courses])

        return ComplianceCheckResult(
            passed=False,
            reasons=[f"Missing prerequisite: {code}" for code in missing_codes],
            details={
                "missing_course_ids": [str(p.prerequisite_course_id) for p in missing],
                "missing_course_codes": missing_codes,
            },
        )

    # ── validate_registration (SDS Table 66) ─────────────────────

    async def validate_registration(
        self,
        session: AsyncSession,
        registration: Registration,
    ) -> ComplianceCheckResult:
        """
        Confirms the registration's total credit load does not exceed
        the SDS-mandated 22 ECTS ceiling. Dropped courses are
        excluded from the sum so a student who drops below the
        ceiling clears the check.

        An empty registration is treated as a failure so the service
        layer can surface "you have no courses selected" cleanly.
        """
        active_course_ids = [
            rc.course_id for rc in registration.courses if not rc.is_dropped
        ]
        if not active_course_ids:
            return ComplianceCheckResult(
                passed=False,
                reasons=["Registration has no active courses."],
                details={"total_credits": 0, "ceiling": self._max_credit_load},
            )

        courses = (
            await session.execute(
                select(Course).where(Course.id.in_(active_course_ids))
            )
        ).scalars().all()
        total_credits = sum(c.credit_hours for c in courses)

        if total_credits > self._max_credit_load:
            return ComplianceCheckResult(
                passed=False,
                reasons=[
                    f"Total credit load {total_credits} ECTS exceeds the "
                    f"{self._max_credit_load} ECTS ceiling per AAU policy."
                ],
                details={
                    "total_credits": total_credits,
                    "ceiling": self._max_credit_load,
                },
            )

        return ComplianceCheckResult(
            passed=True,
            details={
                "total_credits": total_credits,
                "ceiling": self._max_credit_load,
            },
        )

    # ── check_payment_status (SDS Table 66) ──────────────────────

    def check_payment_status(
        self,
        student_id: uuid.UUID,
        course_ids: list[uuid.UUID],
    ) -> ComplianceCheckResult:
        """
        Confirms PayMock has a paid=True flag for every (student,
        course) pair. For government cost-sharing, the form-processing
        service marks all of a student's courses paid in one bulk
        call; for self-sponsored, payments arrive per-credit per-course.
        Either way the agent only sees the per-pair view.
        """
        unpaid = [
            cid for cid in course_ids
            if not self._pay.get_payment_status(student_id, cid)
        ]
        if unpaid:
            return ComplianceCheckResult(
                passed=False,
                reasons=[
                    f"Payment outstanding for {len(unpaid)} course(s); "
                    "registration cannot be finalised until settled."
                ],
                details={"unpaid_course_ids": [str(cid) for cid in unpaid]},
            )
        return ComplianceCheckResult(
            passed=True,
            details={"checked_course_count": len(course_ids)},
        )

    # ── BaseAgent: process_task aggregates the three checks ──────

    async def process_task(
        self, input_data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Run all three checks against a Registration in one call.

        Required keys in ``input_data``:
            session: AsyncSession
            registration: Registration (with .courses eagerly loaded)
            completed_course_ids: set[uuid.UUID]

        Optional keys:
            overridden_course_ids: set[uuid.UUID] — courses with an
                active PrerequisiteOverride; the prereq check skips
                these (SRS §3.5 Department-Head bypass).

        Returns:
            {
                "overall_passed": bool,
                "prereq_results": [
                    {"course_id": str, "passed": bool, "reasons": [str]}
                    ...
                ],
                "load_result":   {"passed": bool, "reasons": [...], "details": ...},
                "payment_result":{"passed": bool, "reasons": [...], "details": ...},
            }
        """
        session: AsyncSession = input_data["session"]
        registration: Registration = input_data["registration"]
        completed: set[uuid.UUID] = input_data.get(
            "completed_course_ids", set()
        )
        overridden: set[uuid.UUID] = input_data.get(
            "overridden_course_ids", set()
        )

        active_course_ids = [
            rc.course_id for rc in registration.courses if not rc.is_dropped
        ]

        prereq_results: list[tuple[uuid.UUID, ComplianceCheckResult]] = []
        for cid in active_course_ids:
            r = await self.verify_prerequisites(
                session, registration.student_id, cid, completed,
                overridden_course_ids=overridden,
            )
            prereq_results.append((cid, r))
        prereq_passed = all(r.passed for _, r in prereq_results)

        load_result = await self.validate_registration(session, registration)
        payment_result = self.check_payment_status(
            registration.student_id, active_course_ids,
        )

        overall_passed = (
            prereq_passed and load_result.passed and payment_result.passed
        )

        return {
            "overall_passed": overall_passed,
            "prereq_results": [
                {
                    "course_id": str(cid),
                    "passed": r.passed,
                    "reasons": r.reasons,
                    "details": r.details,
                }
                for cid, r in prereq_results
            ],
            "load_result": {
                "passed": load_result.passed,
                "reasons": load_result.reasons,
                "details": load_result.details,
            },
            "payment_result": {
                "passed": payment_result.passed,
                "reasons": payment_result.reasons,
                "details": payment_result.details,
            },
        }
