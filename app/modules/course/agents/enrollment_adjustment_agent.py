"""
Enrollment Adjustment Agent.

Realises SDS Tables 79–81:

    Class EnrollmentAdjustmentAgent
        - addDropDeadline: Date
        - adjustmentRules: RuleSet

        + processAddDrop(request: AdjustmentRequest): void
        + validateAdjustmentWindow(): Boolean
        + crossCheckPayment(studentID): Boolean
        + updateSectionCapacity(courseID, action: Enum): void
        + notifyAdjustmentSuccess(studentID): void

The agent owns the *post-registration* policy checks specific to
add/drop requests:
    1. The current date is within the add/drop window.
    2. After applying the change, total credit load is within the
       SDS Table 80 invariant of [12, 22] ECTS.
    3. For ADD: payment is settled in PayMock.
    4. For ADD: a section under the offering has free capacity
       (handled implicitly via :meth:`update_section_capacity`).

Prerequisite checks for an ADD are *not* this agent's responsibility
— the service layer composes the CurriculumComplianceAgent for that
so a single piece of code owns the prereq rule.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.course.agents.course_base_agent import CourseBaseAgent
from app.modules.course.agents.curriculum_compliance_agent import (
    ComplianceCheckResult, MAX_CREDIT_LOAD_ECTS,
)
from app.modules.course.models import (
    AddDropRequest, Course, Registration, Section,
)
from app.modules.course.services import PayMock, pay_mock
from app.shared.enums import AddDropAction


# SDS Table 80 invariant — cannot drop below 12 ECTS.
MIN_CREDIT_LOAD_ECTS = 12


# ── Result container ────────────────────────────────────────────


@dataclass
class AdjustmentResult:
    """
    Structured outcome of :meth:`process_add_drop`. ``approved`` is
    the boolean the surrounding state machine acts on; ``reasons``
    is plain language fed back to the student; ``details`` is
    structured for the audit log.
    """

    approved: bool
    reasons: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


# ── Agent ───────────────────────────────────────────────────────


class EnrollmentAdjustmentAgent(CourseBaseAgent):
    """SDS §3.1.3 + §5.3 Tables 79–81."""

    AGENT_ID_PREFIX = "AGENT_EAA_"

    def __init__(
        self,
        agent_id: Optional[str] = None,
        *,
        payment_service: Optional[PayMock] = None,
        min_credit_load: int = MIN_CREDIT_LOAD_ECTS,
        max_credit_load: int = MAX_CREDIT_LOAD_ECTS,
    ) -> None:
        super().__init__(agent_id=agent_id or f"{self.AGENT_ID_PREFIX}DEFAULT")
        # Explicit `is None` rather than `or` — a caller-supplied PayMock
        # could legitimately be empty, and `or` would silently fall back
        # to the module singleton, breaking test isolation.
        self._pay = payment_service if payment_service is not None else pay_mock
        self._min = min_credit_load
        self._max = max_credit_load

    # ── validate_adjustment_window (SDS Table 81) ────────────────

    def validate_adjustment_window(
        self,
        deadline: date,
        *,
        today: Optional[date] = None,
    ) -> ComplianceCheckResult:
        """
        Pure function: PASS when ``today`` (default :func:`date.today`)
        is on or before ``deadline``.
        """
        today_value = today or date.today()
        if today_value <= deadline:
            return ComplianceCheckResult(
                passed=True,
                details={"deadline": str(deadline), "today": str(today_value)},
            )
        return ComplianceCheckResult(
            passed=False,
            reasons=[
                f"Add/drop deadline ({deadline}) has passed; the window "
                f"is closed as of {today_value}."
            ],
            details={"deadline": str(deadline), "today": str(today_value)},
        )

    # ── cross_check_payment (SDS Table 81) ───────────────────────

    def cross_check_payment(
        self, student_id: uuid.UUID, course_id: uuid.UUID,
    ) -> ComplianceCheckResult:
        """
        For an ADD action: confirm PayMock has the (student, course)
        pair flagged as paid. The check is run only on ADD because a
        DROP never attracts a new fee.
        """
        if self._pay.get_payment_status(student_id, course_id):
            return ComplianceCheckResult(
                passed=True,
                details={"course_id": str(course_id)},
            )
        return ComplianceCheckResult(
            passed=False,
            reasons=[
                "Payment for the course being added is outstanding; the "
                "registration cannot be adjusted until the bursar's "
                "callback marks it paid."
            ],
            details={"unpaid_course_id": str(course_id)},
        )

    # ── update_section_capacity (SDS Table 81) ───────────────────

    async def update_section_capacity(
        self,
        session: AsyncSession,
        section_id: uuid.UUID,
        action: AddDropAction,
        *,
        increment: int = 1,
    ) -> Section:
        """
        Atomically bump a section's enrolled_count for an ADD or
        DROP. ADD raises if the section is already at capacity; DROP
        clamps at zero so a redundant decrement never produces a
        negative count.
        """
        section = await session.get(Section, section_id)
        if section is None:
            raise ValueError(f"Section {section_id} not found.")

        if action == AddDropAction.ADD:
            if section.enrolled_count + increment > section.capacity:
                raise ValueError(
                    f"Section {section_id} is at capacity "
                    f"({section.enrolled_count}/{section.capacity})."
                )
            section.enrolled_count += increment
        else:   # DROP
            section.enrolled_count = max(
                0, section.enrolled_count - increment,
            )
        await session.flush()
        return section

    # ── notify_adjustment_success (SDS Table 81) ─────────────────

    def notify_adjustment_success(
        self, student_id: uuid.UUID, request: AddDropRequest,
    ) -> dict[str, Any]:
        """
        Phase-1 stub: returns a notification payload the service
        layer can hand to the email infrastructure. Phase-2 wiring
        can plug in the existing Brevo provider directly here, but
        keeping email out of the agent body itself preserves
        unit-test simplicity.
        """
        return {
            "to_student_id": str(student_id),
            "subject": "Your course adjustment was processed",
            "body": (
                f"Your request to {request.action.value} course "
                f"{request.course_id} on registration {request.registration_id} "
                "has been applied. Please check your portal for the "
                "updated timetable."
            ),
            "request_id": str(request.id),
        }

    # ── process_add_drop (SDS Table 81) ──────────────────────────

    async def process_add_drop(
        self,
        session: AsyncSession,
        request: AddDropRequest,
        registration: Registration,
        *,
        today: Optional[date] = None,
    ) -> AdjustmentResult:
        """
        The core decision: is this add/drop request allowed?

        Order of checks:
            1. Adjustment window open?
            2. Course exists?
            3. Post-change credit load within [_min, _max]?
            4. For ADD: payment cross-check.

        Returns an :class:`AdjustmentResult`. The service layer
        applies the actual mutation when ``approved`` is True; the
        agent does not flip request status itself.
        """
        # 1. Window
        window = self.validate_adjustment_window(
            request.deadline_snapshot, today=today,
        )
        if not window.passed:
            return AdjustmentResult(
                approved=False, reasons=window.reasons, details=window.details,
            )

        # 2. Course
        course = await session.get(Course, request.course_id)
        if course is None:
            return AdjustmentResult(
                approved=False,
                reasons=[f"Course {request.course_id} does not exist."],
            )

        # 3. Post-change credit load
        active_credits = await self._active_credit_total(session, registration)
        if request.action == AddDropAction.ADD:
            new_total = active_credits + course.credit_hours
            if new_total > self._max:
                return AdjustmentResult(
                    approved=False,
                    reasons=[
                        f"Adding {course.code} would push credit load to "
                        f"{new_total} ECTS (ceiling: {self._max})."
                    ],
                    details={"new_total": new_total, "ceiling": self._max},
                )
            # 4. Payment
            payment = self.cross_check_payment(
                registration.student_id, request.course_id,
            )
            if not payment.passed:
                return AdjustmentResult(
                    approved=False,
                    reasons=payment.reasons,
                    details=payment.details,
                )
            return AdjustmentResult(
                approved=True,
                details={"new_total": new_total, "course_code": course.code},
            )

        # DROP
        new_total = active_credits - course.credit_hours
        if new_total < self._min:
            return AdjustmentResult(
                approved=False,
                reasons=[
                    f"Dropping {course.code} would lower credit load to "
                    f"{new_total} ECTS (floor: {self._min})."
                ],
                details={"new_total": new_total, "floor": self._min},
            )
        return AdjustmentResult(
            approved=True,
            details={"new_total": new_total, "course_code": course.code},
        )

    async def _active_credit_total(
        self, session: AsyncSession, registration: Registration,
    ) -> int:
        """Sum credit hours across the registration's non-dropped courses."""
        await session.refresh(registration, attribute_names=["courses"])
        active_course_ids = [
            rc.course_id for rc in registration.courses if not rc.is_dropped
        ]
        if not active_course_ids:
            return 0
        rows = (
            await session.execute(
                select(Course).where(Course.id.in_(active_course_ids))
            )
        ).scalars().all()
        return sum(c.credit_hours for c in rows)

    # ── BaseAgent: process_task ─────────────────────────────────

    async def process_task(
        self, input_data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Run :meth:`process_add_drop` and shape the result into a
        plain dict the service layer drops into the audit-log
        metadata column.
        """
        result = await self.process_add_drop(
            session=input_data["session"],
            request=input_data["request"],
            registration=input_data["registration"],
            today=input_data.get("today"),
        )
        return {
            "approved": result.approved,
            "reasons": result.reasons,
            "details": result.details,
        }
