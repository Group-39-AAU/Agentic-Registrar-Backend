"""
Course Management — service layer (Track A foundation).

Owns the SDS Figure 39 registration state machine. Composes the
repository helpers and the CurriculumComplianceAgent so the router
remains thin.

Trilogy of persistence on every state change:
    1. Update Registration.status.
    2. Append a RegistrationStatusHistory row.
    3. Write a structured stdout audit-log entry.

The agent itself adds a SystemAuditLog row inside its _log_action
hook when it makes a decision; this service writes the surrounding
state-machine transitions.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger, write_audit_log
from app.modules.course.agents import CurriculumComplianceAgent
from app.modules.course.exceptions import (
    ComplianceCheckFailedError,
    DuplicateRegistrationError,
    EntityNotFoundError,
    InvalidStateTransitionError,
    RegistrationWindowClosedError,
    UnauthorizedActorError,
)
from app.modules.course.models import (
    AcademicTerm, Course, Registration, RegistrationCourse,
    RegistrationStatusHistory, Student,
)
from app.modules.course.repository import (
    AcademicTermRepository, CourseRepository, RegistrationRepository,
    StudentRepository,
)
from app.shared.enums import (
    RegistrationStatus, SponsorshipType, UserRole,
)

logger = get_logger("course.service")


# ── Allowed transitions in the SDS Figure 39 state machine ───────
_ALLOWED_TRANSITIONS: dict[RegistrationStatus, set[RegistrationStatus]] = {
    RegistrationStatus.REGISTRATION_OPEN: {
        RegistrationStatus.ADVISOR_REVIEW,
        RegistrationStatus.CHECKING_PREREQUISITES,
        RegistrationStatus.CANCELLED,
    },
    RegistrationStatus.ADVISOR_REVIEW: {
        RegistrationStatus.CHECKING_PREREQUISITES,
        RegistrationStatus.CANCELLED,
    },
    RegistrationStatus.CHECKING_PREREQUISITES: {
        RegistrationStatus.CHECKING_PAYMENT,
        RegistrationStatus.REGISTRATION_OPEN,   # back-to-draft on failure
        RegistrationStatus.CANCELLED,
    },
    RegistrationStatus.CHECKING_PAYMENT: {
        RegistrationStatus.VALIDATION_SUCCESS,
        RegistrationStatus.PAYMENT_HOLD,
        RegistrationStatus.CANCELLED,
    },
    RegistrationStatus.PAYMENT_HOLD: {
        RegistrationStatus.CHECKING_PAYMENT,
        RegistrationStatus.CANCELLED,
    },
    RegistrationStatus.VALIDATION_SUCCESS: {
        RegistrationStatus.REGISTERED,
        RegistrationStatus.CANCELLED,
    },
    RegistrationStatus.REGISTERED: {
        RegistrationStatus.ADD_DROP_WINDOW,
    },
    RegistrationStatus.ADD_DROP_WINDOW: set(),
    RegistrationStatus.CANCELLED: set(),
}


class TermService:
    """Officer-only window control."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.terms = AcademicTermRepository(db)

    async def open_window(
        self, term_id: uuid.UUID, officer_role: UserRole, officer_id: uuid.UUID,
    ) -> AcademicTerm:
        if officer_role not in {UserRole.REGISTRAR_OFFICER, UserRole.ADMIN}:
            raise UnauthorizedActorError(
                "Only registrar officers or admins can open a term."
            )
        term = await self.terms.get(term_id)
        if term is None:
            raise EntityNotFoundError("AcademicTerm", str(term_id))
        if term.is_open:
            return term
        term.is_open = True
        await self.db.commit()
        await self.db.refresh(term)
        write_audit_log(
            action="course.term.opened",
            actor_role=officer_role.value,
            actor_id=officer_id,
            resource_type="AcademicTerm",
            resource_id=term.id,
        )
        return term

    async def close_window(
        self, term_id: uuid.UUID, officer_role: UserRole, officer_id: uuid.UUID,
    ) -> AcademicTerm:
        if officer_role not in {UserRole.REGISTRAR_OFFICER, UserRole.ADMIN}:
            raise UnauthorizedActorError(
                "Only registrar officers or admins can close a term."
            )
        term = await self.terms.get(term_id)
        if term is None:
            raise EntityNotFoundError("AcademicTerm", str(term_id))
        if not term.is_open:
            return term
        term.is_open = False
        await self.db.commit()
        await self.db.refresh(term)
        write_audit_log(
            action="course.term.closed",
            actor_role=officer_role.value,
            actor_id=officer_id,
            resource_type="AcademicTerm",
            resource_id=term.id,
        )
        return term


class RegistrationService:
    """
    Drives the SDS Figure 39 state machine for a single registration.

    The CurriculumComplianceAgent is injected so tests can pass an
    agent backed by an isolated PayMock. In production
    ``CurriculumComplianceAgent()`` is constructed with the module-
    level pay_mock singleton.
    """

    def __init__(
        self,
        db: AsyncSession,
        *,
        compliance_agent: Optional[CurriculumComplianceAgent] = None,
    ) -> None:
        self.db = db
        self.terms = AcademicTermRepository(db)
        self.courses = CourseRepository(db)
        self.students = StudentRepository(db)
        self.registrations = RegistrationRepository(db)
        self.compliance_agent = compliance_agent or CurriculumComplianceAgent()

    # ── State-machine helper ─────────────────────────────────────

    async def _transition(
        self,
        registration: Registration,
        target: RegistrationStatus,
        *,
        changed_by_id: Optional[uuid.UUID] = None,
        agent_id: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        """
        Apply a transition with the trilogy of persistence: update
        the registration status, append a status_history row, emit
        an audit log entry.
        """
        current = registration.status
        if target not in _ALLOWED_TRANSITIONS.get(current, set()):
            raise InvalidStateTransitionError(current.value, target.value)
        previous = current
        registration.status = target
        self.db.add(
            RegistrationStatusHistory(
                registration_id=registration.id,
                previous_status=previous,
                new_status=target,
                changed_by_id=changed_by_id,
                agent_id=agent_id,
                trigger_reason=reason,
            )
        )
        write_audit_log(
            action="course.registration.transition",
            actor_role=UserRole.AGENT.value if agent_id else UserRole.STUDENT.value,
            actor_id=changed_by_id,
            resource_type="Registration",
            resource_id=registration.id,
            decision=target.value,
            metadata={
                "previous": previous.value,
                "agent_id": agent_id,
                "reason": reason,
            },
        )

    # ── Curriculum read for the portal ───────────────────────────

    async def list_curriculum_courses(
        self, student_id: uuid.UUID,
    ) -> list[Course]:
        """
        Return the courses the calling student is allowed to see in
        the portal. Phase-1 simplification: every course in the
        catalog. The full SRS Course-FR-01 curriculum filter
        (program-aligned, semester-aligned) lands in Track A.5 with
        the AcademicAdvisoryAgent.
        """
        student = (
            await self.db.execute(
                select(Student).where(Student.id == student_id)
            )
        ).scalar_one_or_none()
        if student is None:
            raise EntityNotFoundError("Student", str(student_id))
        return list(
            (
                await self.db.execute(
                    select(Course).where(Course.is_deleted == False)  # noqa: E712
                )
            ).scalars().all()
        )

    # ── Draft management ─────────────────────────────────────────

    async def create_draft(
        self,
        student_id: uuid.UUID,
        term_id: uuid.UUID,
        sponsorship_type: SponsorshipType,
    ) -> Registration:
        term = await self.terms.get(term_id)
        if term is None:
            raise EntityNotFoundError("AcademicTerm", str(term_id))
        if not term.is_open:
            raise RegistrationWindowClosedError(term.term_name)

        existing = await self.registrations.get_for_student_and_term(
            student_id, term_id,
        )
        if existing is not None:
            raise DuplicateRegistrationError(str(student_id), str(term_id))

        registration = Registration(
            student_id=student_id,
            term_id=term_id,
            status=RegistrationStatus.REGISTRATION_OPEN,
            sponsorship_type=sponsorship_type,
        )
        self.db.add(registration)
        await self.db.flush()

        # Seed history with the initial state for replay reconstruction.
        self.db.add(
            RegistrationStatusHistory(
                registration_id=registration.id,
                previous_status=None,
                new_status=RegistrationStatus.REGISTRATION_OPEN,
                changed_by_id=None,
                trigger_reason="initial draft",
            )
        )
        await self.db.commit()
        await self.db.refresh(registration, attribute_names=["courses"])
        return registration

    async def add_course_to_draft(
        self, registration_id: uuid.UUID, course_id: uuid.UUID,
    ) -> Registration:
        registration = await self._draft_or_raise(registration_id)
        course = await self.courses.get(course_id)
        if course is None:
            raise EntityNotFoundError("Course", str(course_id))

        if await self.registrations.get_course_link(registration_id, course_id):
            return registration   # idempotent: already added

        self.db.add(
            RegistrationCourse(
                registration_id=registration_id,
                course_id=course_id,
            )
        )
        await self.db.commit()
        await self.db.refresh(registration, attribute_names=["courses"])
        return registration

    async def remove_course_from_draft(
        self, registration_id: uuid.UUID, course_id: uuid.UUID,
    ) -> Registration:
        registration = await self._draft_or_raise(registration_id)
        link = await self.registrations.get_course_link(
            registration_id, course_id,
        )
        if link is None:
            raise EntityNotFoundError(
                "RegistrationCourse",
                f"registration={registration_id} course={course_id}",
            )
        await self.db.delete(link)
        await self.db.commit()
        await self.db.refresh(registration, attribute_names=["courses"])
        return registration

    async def _draft_or_raise(
        self, registration_id: uuid.UUID,
    ) -> Registration:
        registration = await self.registrations.get(registration_id)
        if registration is None:
            raise EntityNotFoundError("Registration", str(registration_id))
        if registration.status not in {
            RegistrationStatus.REGISTRATION_OPEN,
            RegistrationStatus.ADVISOR_REVIEW,
        }:
            raise InvalidStateTransitionError(
                registration.status.value, "draft mutation",
            )
        return registration

    # ── Submit (the spine) ───────────────────────────────────────

    async def submit(
        self,
        registration_id: uuid.UUID,
        student_user_id: uuid.UUID,
        completed_course_ids: Optional[set[uuid.UUID]] = None,
    ) -> tuple[Registration, dict]:
        """
        Drive the registration through the SDS Figure 39 lifecycle:

            REGISTRATION_OPEN
                -> CHECKING_PREREQUISITES
                -> CHECKING_PAYMENT
                -> VALIDATION_SUCCESS -> REGISTERED       (happy path)
                -> PAYMENT_HOLD                           (payment missing)
                -> back to REGISTRATION_OPEN              (prereq/load fail)

        Returns the post-submit registration plus the agent's
        compliance payload so the router can echo plain-language
        reasons back to the student.
        """
        registration = await self.registrations.get(registration_id)
        if registration is None:
            raise EntityNotFoundError("Registration", str(registration_id))
        if registration.status not in {
            RegistrationStatus.REGISTRATION_OPEN,
            RegistrationStatus.ADVISOR_REVIEW,
        }:
            raise InvalidStateTransitionError(
                registration.status.value, "submit",
            )

        # Eager-load courses for the agent.
        await self.db.refresh(registration, attribute_names=["courses"])

        # 1. Move into CHECKING_PREREQUISITES.
        await self._transition(
            registration,
            RegistrationStatus.CHECKING_PREREQUISITES,
            changed_by_id=student_user_id,
            reason="student submitted",
        )

        # 2. Run the CurriculumComplianceAgent.
        compliance = await self.compliance_agent.process_task({
            "session": self.db,
            "registration": registration,
            "completed_course_ids": completed_course_ids or set(),
        })

        prereq_passed = all(p["passed"] for p in compliance["prereq_results"])
        load_passed = compliance["load_result"]["passed"]
        payment_passed = compliance["payment_result"]["passed"]

        if not (prereq_passed and load_passed):
            # Send the registration back to draft with a structured reason.
            await self._transition(
                registration,
                RegistrationStatus.REGISTRATION_OPEN,
                agent_id=self.compliance_agent.agent_id,
                reason="prerequisite or credit-load check failed",
            )
            await self.db.commit()
            await self.db.refresh(registration, attribute_names=["courses"])
            raise ComplianceCheckFailedError(compliance)

        # 3. Move into CHECKING_PAYMENT.
        await self._transition(
            registration,
            RegistrationStatus.CHECKING_PAYMENT,
            agent_id=self.compliance_agent.agent_id,
            reason="prerequisite + load checks passed",
        )

        # 4. Branch on payment.
        if not payment_passed:
            await self._transition(
                registration,
                RegistrationStatus.PAYMENT_HOLD,
                agent_id=self.compliance_agent.agent_id,
                reason="payment outstanding",
            )
            await self.db.commit()
            await self.db.refresh(registration, attribute_names=["courses"])
            return registration, compliance

        # 5. Happy path: VALIDATION_SUCCESS -> REGISTERED.
        await self._transition(
            registration,
            RegistrationStatus.VALIDATION_SUCCESS,
            agent_id=self.compliance_agent.agent_id,
            reason="all compliance checks passed",
        )
        await self._transition(
            registration,
            RegistrationStatus.REGISTERED,
            agent_id=self.compliance_agent.agent_id,
            reason="finalised",
        )
        registration.finalised_at = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(registration, attribute_names=["courses"])
        return registration, compliance
