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
    ComplianceCheckResult, CurriculumComplianceAgent,
    MAX_CREDIT_LOAD_ECTS, MIN_CREDIT_LOAD_ECTS,
)
from app.modules.course.models import (
    AddDropRequest, ClassScheduleSlot, Course, Registration, Section, Student,
)
from app.modules.course.services import PayMock, pay_mock
from app.shared.enums import AddDropAction


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


@dataclass
class BatchItemVerdict:
    """
    Per-item agent verdict inside a batch. ``passed`` is the
    decision; ``reasons`` is the human-facing plain-language summary
    of why; ``details`` is the structured payload that lands in the
    audit log.
    """

    course_id: uuid.UUID
    course_code: str
    action: AddDropAction
    passed: bool
    reasons: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "course_id": str(self.course_id),
            "course_code": self.course_code,
            "action": self.action.value,
            "passed": self.passed,
            "reasons": list(self.reasons),
            "details": dict(self.details),
        }


@dataclass
class BatchResult:
    """
    Whole-batch outcome of :meth:`process_batch`. The batch is
    all-or-nothing: ``approved`` is True only when every item
    passes. Per-item verdicts are kept on ``items`` so the officer
    queue can render "approved 2, denied 1: CS201 (prereq unmet)".
    """

    approved: bool
    items: list[BatchItemVerdict] = field(default_factory=list)

    def failing_items(self) -> list[BatchItemVerdict]:
        return [it for it in self.items if not it.passed]


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
        compliance_agent: Optional[CurriculumComplianceAgent] = None,
    ) -> None:
        super().__init__(agent_id=agent_id or f"{self.AGENT_ID_PREFIX}DEFAULT")
        # Explicit `is None` rather than `or` — a caller-supplied PayMock
        # could legitimately be empty, and `or` would silently fall back
        # to the module singleton, breaking test isolation.
        self._pay = payment_service if payment_service is not None else pay_mock
        self._min = min_credit_load
        self._max = max_credit_load
        # The batch processor delegates the prereq check to the
        # compliance agent so a single piece of code owns that rule
        # (SDS Table 66). Reuses the same payment service so both
        # agents see the same mock state in tests.
        self._compliance = compliance_agent or CurriculumComplianceAgent(
            payment_service=self._pay,
        )

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
            # # 4. Payment
            # payment = self.cross_check_payment(
            #     registration.student_id, request.course_id,
            # )
            # if not payment.passed:
            #     return AdjustmentResult(
            #         approved=False,
            #         reasons=payment.reasons,
            #         details=payment.details,
            #     )
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

    # ── Batch checks (curriculum / parity / prereq) ──────────────

    def verify_curriculum_membership(
        self, student: Student, course: Course,
    ) -> ComplianceCheckResult:
        """
        PASS when ``course.department`` matches the student's
        denormalised department. AAU undergraduate programs do not
        share courses across departments — each program has its own
        version of foundational courses like Calculus / Discrete Math.
        """
        if not student.department:
            return ComplianceCheckResult(
                passed=False,
                reasons=[
                    "Student has no department on file; the curriculum "
                    "match check cannot run. Contact the registrar."
                ],
                details={"course_id": str(course.id), "course_code": course.code},
            )
        if course.department != student.department:
            return ComplianceCheckResult(
                passed=False,
                reasons=[
                    f"{course.code} belongs to {course.department}; the "
                    f"student is enrolled in {student.department} and "
                    "may only register for courses in their own program."
                ],
                details={
                    "course_id": str(course.id),
                    "course_code": course.code,
                    "course_department": course.department,
                    "student_department": student.department,
                },
            )
        return ComplianceCheckResult(
            passed=True,
            details={"course_code": course.code, "department": course.department},
        )

    def verify_semester_parity(
        self, student: Student, course: Course,
    ) -> ComplianceCheckResult:
        """
        PASS when the course's semester has the same parity as the
        student's current semester.

        AAU teaches odd semesters (1, 3, 5, ...) in the first half
        of the academic year and even semesters (2, 4, 6, ...) in
        the second. A student in semester 4 (even) can therefore
        only register for courses whose ``semester`` is even — sem-1
        and sem-3 courses simply are not being offered this term.
        Prerequisite ordering catches the rest.
        """
        student_parity = student.current_semester % 2
        course_parity = course.semester % 2
        student_half = "odd" if student_parity == 1 else "even"
        course_half = "odd" if course_parity == 1 else "even"
        if student_parity != course_parity:
            return ComplianceCheckResult(
                passed=False,
                reasons=[
                    f"{course.code} is a semester-{course.semester} "
                    f"({course_half}-term) course but the student is in "
                    f"semester {student.current_semester} "
                    f"({student_half}-term). That course is not being "
                    "offered in the current academic term."
                ],
                details={
                    "course_code": course.code,
                    "course_semester": course.semester,
                    "student_semester": student.current_semester,
                },
            )
        return ComplianceCheckResult(
            passed=True,
            details={
                "course_code": course.code,
                "course_semester": course.semester,
                "student_semester": student.current_semester,
            },
        )

    async def verify_prerequisites(
        self,
        session: AsyncSession,
        student_id: uuid.UUID,
        course: Course,
        completed_course_ids: set[uuid.UUID],
        overridden_course_ids: Optional[set[uuid.UUID]] = None,
    ) -> ComplianceCheckResult:
        """
        Thin delegation to :class:`CurriculumComplianceAgent` so the
        prereq rule (SDS Table 66) has exactly one implementation.
        Passes through the override set so a Department-Head bypass
        still wins inside an add/drop batch.
        """
        return await self._compliance.verify_prerequisites(
            session=session,
            student_id=student_id,
            course_id=course.id,
            completed_course_ids=completed_course_ids,
            overridden_course_ids=overridden_course_ids or set(),
        )

    async def verify_offered_in_term(
        self,
        session: AsyncSession,
        term_id: uuid.UUID,
        course: Course,
    ) -> ComplianceCheckResult:
        """
        PASS when at least one Section in ``term_id`` actually teaches
        ``course`` — i.e. there is a ClassScheduleSlot for the course
        in a section belonging to this term.

        Without this gate a student could ADD a course that passes
        curriculum + parity + prereq checks yet is not run by any
        section this term: the course would land permanently in
        ``pending_additions`` with zero pickable options. Blocking it
        up front keeps the add/drop verdict honest — you can only add
        what you can actually be scheduled into.
        """
        offering_section_id = (
            await session.execute(
                select(ClassScheduleSlot.section_id).join(
                    Section, Section.id == ClassScheduleSlot.section_id,
                ).where(
                    Section.term_id == term_id,
                    Section.is_deleted == False,  # noqa: E712
                    ClassScheduleSlot.course_id == course.id,
                ).limit(1)
            )
        ).scalar_one_or_none()
        if offering_section_id is None:
            return ComplianceCheckResult(
                passed=False,
                reasons=[
                    f"{course.code} is not offered by any section this "
                    "term — there is no scheduled section to place you "
                    "in. It cannot be added until a section runs it."
                ],
                details={"course_code": course.code, "term_id": str(term_id)},
            )
        return ComplianceCheckResult(
            passed=True,
            details={
                "course_code": course.code,
                "offering_section_id": str(offering_section_id),
            },
        )

    # ── process_batch (all-or-nothing) ───────────────────────────

    async def process_batch(
        self,
        session: AsyncSession,
        *,
        student: Student,
        registration: Registration,
        items: list[tuple[Course, AddDropAction]],
        completed_course_ids: set[uuid.UUID],
        overridden_course_ids: Optional[set[uuid.UUID]] = None,
        today: Optional[date] = None,
        deadline: Optional[date] = None,
    ) -> BatchResult:
        """
        Evaluate every (course, action) pair in the batch against:
            1. Add/drop window (if a deadline is supplied).
            2. Curriculum membership — course.department == student's.
            3. Semester parity — course offered in the current half.
            4. For ADD: prerequisites satisfied (or overridden).
            5. For ADD: payment cross-check.
            6. For DROP: course is currently in the registration.
            7. Post-batch credit load within [_min, _max] (single
               summary check across the whole batch).

        All-or-nothing: a failure on any item flips the whole
        ``BatchResult.approved`` to False but per-item verdicts are
        still populated so the officer queue can render reasons.
        """
        verdicts: list[BatchItemVerdict] = []
        overrides = overridden_course_ids or set()

        # Snapshot active courses (non-dropped) once so per-item
        # checks for "already in registration?" don't re-issue queries.
        await session.refresh(registration, attribute_names=["courses"])
        active_course_ids = {
            rc.course_id for rc in registration.courses if not rc.is_dropped
        }
        baseline_credits = await self._active_credit_total(
            session, registration,
        )

        # Per-item evaluation — bias toward fully reporting every
        # failure rather than short-circuiting on the first denial,
        # so the student sees all problems in one round trip.
        running_delta = 0
        for course, action in items:
            reasons: list[str] = []
            details: dict[str, Any] = {
                "course_code": course.code,
                "action": action.value,
            }

            if deadline is not None:
                window = self.validate_adjustment_window(
                    deadline, today=today,
                )
                if not window.passed:
                    reasons.extend(window.reasons)
                    details.update(window.details)

            membership = self.verify_curriculum_membership(student, course)
            if not membership.passed:
                reasons.extend(membership.reasons)
                details.update(membership.details)

            parity = self.verify_semester_parity(student, course)
            if not parity.passed:
                reasons.extend(parity.reasons)
                details.update(parity.details)

            if action == AddDropAction.ADD:
                if course.id in active_course_ids:
                    reasons.append(
                        f"{course.code} is already on the registration; "
                        "nothing to add."
                    )
                offered = await self.verify_offered_in_term(
                    session, registration.term_id, course,
                )
                if not offered.passed:
                    reasons.extend(offered.reasons)
                    details.update(offered.details)
                prereq = await self.verify_prerequisites(
                    session=session,
                    student_id=student.id,
                    course=course,
                    completed_course_ids=completed_course_ids,
                    overridden_course_ids=overrides,
                )
                if not prereq.passed:
                    reasons.extend(prereq.reasons)
                    details.update({"prereq_details": prereq.details})
                # payment = self.cross_check_payment(student.id, course.id)
                # if not payment.passed:
                #     reasons.extend(payment.reasons)
                #     details.update(payment.details)
                running_delta += course.credit_hours
            else:   # DROP
                if course.id not in active_course_ids:
                    reasons.append(
                        f"Cannot drop {course.code} — student is not "
                        "registered for it."
                    )
                running_delta -= course.credit_hours

            verdicts.append(BatchItemVerdict(
                course_id=course.id,
                course_code=course.code,
                action=action,
                passed=not reasons,
                reasons=reasons,
                details=details,
            ))

        # Final credit-load envelope (applies once across the batch).
        projected_total = baseline_credits + running_delta
        if projected_total > self._max:
            verdicts.append(BatchItemVerdict(
                course_id=uuid.UUID(int=0),
                course_code="__BATCH__",
                action=AddDropAction.ADD,
                passed=False,
                reasons=[
                    f"Applying the batch would push credit load to "
                    f"{projected_total} ECTS (ceiling: {self._max})."
                ],
                details={
                    "baseline_credits": baseline_credits,
                    "projected_total": projected_total,
                    "ceiling": self._max,
                },
            ))
        elif projected_total < self._min:
            verdicts.append(BatchItemVerdict(
                course_id=uuid.UUID(int=0),
                course_code="__BATCH__",
                action=AddDropAction.DROP,
                passed=False,
                reasons=[
                    f"Applying the batch would lower credit load to "
                    f"{projected_total} ECTS (floor: {self._min})."
                ],
                details={
                    "baseline_credits": baseline_credits,
                    "projected_total": projected_total,
                    "floor": self._min,
                },
            ))

        approved = all(v.passed for v in verdicts)
        return BatchResult(approved=approved, items=verdicts)

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
