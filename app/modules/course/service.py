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
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger, write_audit_log
from app.modules.course.agents import (
    AcademicSchedulingAgent, CurriculumComplianceAgent,
    EnrollmentAdjustmentAgent,
)
from app.modules.course.exceptions import (
    AdjustmentDeniedError,
    ComplianceCheckFailedError,
    DuplicateRegistrationError,
    EntityNotFoundError,
    InvalidAdjustmentRequestError,
    InvalidStateTransitionError,
    RegistrationWindowClosedError,
    UnauthorizedActorError,
)
from app.modules.course.models import (
    AcademicTerm, AddDropRequest, Course, CourseOffering, Instructor,
    Registration, RegistrationCourse, RegistrationStatusHistory,
    ScheduleConflict, Section, Student,
)
from app.modules.course.repository import (
    AcademicTermRepository, AddDropRequestRepository, CourseRepository,
    RegistrationRepository, StudentRepository,
)
from app.shared.enums import (
    AddDropAction, AddDropRequestStatus, RegistrationStatus,
    SponsorshipType, UserRole,
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


class SchedulingService:
    """
    Officer-triggered schedule generation plus the read-only
    timetable views consumed by students and instructors. Wraps the
    AcademicSchedulingAgent and owns the transaction boundary so the
    router stays thin.
    """

    def __init__(
        self,
        db: AsyncSession,
        *,
        scheduling_agent: Optional[AcademicSchedulingAgent] = None,
    ) -> None:
        self.db = db
        self.terms = AcademicTermRepository(db)
        self.scheduling_agent = scheduling_agent or AcademicSchedulingAgent()

    # ── Officer trigger ──────────────────────────────────────────

    async def generate_schedule(
        self,
        term_id: uuid.UUID,
        department: str,
        officer_role: UserRole,
        officer_id: uuid.UUID,
    ) -> dict:
        """
        Run the full scheduling pipeline (allocate + timetable) for
        a (term, department) pair. Officer-only.
        """
        if officer_role not in {UserRole.REGISTRAR_OFFICER, UserRole.ADMIN}:
            raise UnauthorizedActorError(
                "Only registrar officers or admins can generate schedules."
            )
        term = await self.terms.get(term_id)
        if term is None:
            raise EntityNotFoundError("AcademicTerm", str(term_id))

        payload = await self.scheduling_agent.process_task({
            "session": self.db,
            "term_id": term_id,
            "department": department,
        })
        await self.db.commit()

        write_audit_log(
            action="course.schedule.generated",
            actor_role=officer_role.value,
            actor_id=officer_id,
            resource_type="AcademicTerm",
            resource_id=term_id,
            decision="ok",
            metadata={
                "department": department,
                "allocated": payload["allocation"]["allocated_count"],
                "failed": payload["allocation"]["failed_count"],
                "conflicts": payload["schedule"]["conflict_count"],
            },
        )
        return payload

    # ── Read views ───────────────────────────────────────────────

    async def get_student_timetable(
        self,
        student_id: uuid.UUID,
        term_id: uuid.UUID,
    ) -> list[dict]:
        """
        Return the student's per-section schedule for the term: only
        rows whose section_id is set (i.e. allocate_sections has run)
        and whose registration is not cancelled.
        """
        rows = (
            await self.db.execute(
                select(RegistrationCourse, Section, Course)
                .join(Section, RegistrationCourse.section_id == Section.id)
                .join(CourseOffering, Section.offering_id == CourseOffering.id)
                .join(Course, CourseOffering.course_id == Course.id)
                .join(Registration, RegistrationCourse.registration_id == Registration.id)
                .where(
                    Registration.student_id == student_id,
                    Registration.term_id == term_id,
                    Registration.is_deleted == False,  # noqa: E712
                    RegistrationCourse.is_dropped == False,  # noqa: E712
                    RegistrationCourse.section_id.is_not(None),
                )
            )
        ).all()
        return [
            {
                "section_id": sec.id,
                "course_code": course.code,
                "course_title": course.title,
                "section_code": sec.section_code,
                "room": sec.room,
                "time_slot": sec.time_slot,
                "instructor_id": sec.instructor_id,
            }
            for _rc, sec, course in rows
        ]

    async def get_instructor_timetable(
        self,
        instructor_id: uuid.UUID,
        term_id: uuid.UUID,
    ) -> list[dict]:
        """Return every section assigned to this instructor for the term."""
        rows = (
            await self.db.execute(
                select(Section, Course)
                .join(CourseOffering, Section.offering_id == CourseOffering.id)
                .join(Course, CourseOffering.course_id == Course.id)
                .where(
                    CourseOffering.term_id == term_id,
                    Section.instructor_id == instructor_id,
                    Section.is_deleted == False,  # noqa: E712
                )
            )
        ).all()
        return [
            {
                "section_id": sec.id,
                "course_code": course.code,
                "course_title": course.title,
                "section_code": sec.section_code,
                "room": sec.room,
                "time_slot": sec.time_slot,
                "instructor_id": sec.instructor_id,
            }
            for sec, course in rows
        ]

    async def list_open_conflicts(
        self,
        term_id: uuid.UUID,
        officer_role: UserRole,
        department: Optional[str] = None,
    ) -> list[ScheduleConflict]:
        """Officer-only: list every OPEN ScheduleConflict in the term."""
        if officer_role not in {UserRole.REGISTRAR_OFFICER, UserRole.ADMIN}:
            raise UnauthorizedActorError(
                "Only registrar officers or admins can view the conflict report."
            )
        from app.shared.enums import ScheduleConflictStatus
        stmt = select(ScheduleConflict).where(
            ScheduleConflict.term_id == term_id,
            ScheduleConflict.status == ScheduleConflictStatus.OPEN,
        )
        if department:
            stmt = stmt.where(ScheduleConflict.department == department)
        return list((await self.db.execute(stmt)).scalars().all())


class AddDropService:
    """
    Drives the AddDropRequest lifecycle:

        PENDING
          -> APPROVED -> APPLIED            (agent approves)
          -> DENIED                         (agent blocks)
              -> OVERRIDDEN -> APPLIED      (officer override)

    The EnrollmentAdjustmentAgent makes the policy decision; this
    service is responsible for persisting the outcome, applying the
    actual change to the registration, and writing the audit trail.
    """

    def __init__(
        self,
        db: AsyncSession,
        *,
        adjustment_agent: Optional[EnrollmentAdjustmentAgent] = None,
    ) -> None:
        self.db = db
        self.registrations = RegistrationRepository(db)
        self.requests = AddDropRequestRepository(db)
        self.agent = adjustment_agent or EnrollmentAdjustmentAgent()

    # ── Submit (the spine) ──────────────────────────────────────

    async def submit_request(
        self,
        registration_id: uuid.UUID,
        course_id: uuid.UUID,
        action: AddDropAction,
        deadline: date,
        student_user_id: uuid.UUID,
        target_section_id: Optional[uuid.UUID] = None,
    ) -> AddDropRequest:
        """
        Persist a new AddDropRequest, run the agent, and either:
            • apply the change and flip status to APPLIED, or
            • flip status to DENIED and raise AdjustmentDeniedError
              with the agent payload.
        """
        registration = await self.registrations.get(registration_id)
        if registration is None:
            raise EntityNotFoundError("Registration", str(registration_id))
        if registration.status not in {
            RegistrationStatus.REGISTERED,
            RegistrationStatus.ADD_DROP_WINDOW,
        }:
            raise InvalidAdjustmentRequestError(
                f"Registration is in {registration.status.value}; only "
                "REGISTERED or ADD_DROP_WINDOW registrations accept add/drop."
            )

        request = AddDropRequest(
            registration_id=registration_id,
            course_id=course_id,
            target_section_id=target_section_id,
            action=action,
            deadline_snapshot=deadline,
            status=AddDropRequestStatus.PENDING,
        )
        self.db.add(request)
        await self.db.flush()

        await self._run_agent_and_apply(
            request, registration, student_user_id,
        )
        await self.db.commit()
        await self.db.refresh(request)
        return request

    async def _run_agent_and_apply(
        self,
        request: AddDropRequest,
        registration: Registration,
        actor_id: uuid.UUID,
    ) -> None:
        result = await self.agent.process_add_drop(
            self.db, request, registration,
        )
        if not result.approved:
            request.status = AddDropRequestStatus.DENIED
            request.reason = "; ".join(result.reasons)
            write_audit_log(
                action="course.add_drop.denied",
                actor_role=UserRole.AGENT.value,
                actor_id=None,
                resource_type="AddDropRequest",
                resource_id=request.id,
                decision=AddDropRequestStatus.DENIED.value,
                metadata={
                    "agent_id": self.agent.agent_id,
                    "reasons": result.reasons,
                    "details": result.details,
                },
            )
            raise AdjustmentDeniedError({
                "approved": False,
                "reasons": result.reasons,
                "details": result.details,
            })

        request.status = AddDropRequestStatus.APPROVED
        await self._apply_approved_change(request, registration)
        request.status = AddDropRequestStatus.APPLIED
        write_audit_log(
            action="course.add_drop.applied",
            actor_role=UserRole.AGENT.value,
            actor_id=actor_id,
            resource_type="AddDropRequest",
            resource_id=request.id,
            decision=AddDropRequestStatus.APPLIED.value,
            metadata={
                "agent_id": self.agent.agent_id,
                "details": result.details,
            },
        )

    async def _apply_approved_change(
        self, request: AddDropRequest, registration: Registration,
    ) -> None:
        """Materialise an approved request as a RegistrationCourse mutation."""
        if request.action == AddDropAction.ADD:
            await self._apply_add(request, registration)
        else:
            await self._apply_drop(request, registration)

    async def _apply_add(
        self, request: AddDropRequest, registration: Registration,
    ) -> None:
        # Find an offering for this course in the term.
        offering = (
            await self.db.execute(
                select(CourseOffering).where(
                    CourseOffering.course_id == request.course_id,
                    CourseOffering.term_id == registration.term_id,
                    CourseOffering.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()
        if offering is None:
            raise InvalidAdjustmentRequestError(
                f"Course {request.course_id} has no offering for term "
                f"{registration.term_id}."
            )

        # Pick a section: caller-specified target_section_id wins, else
        # any section under the offering with free capacity.
        section: Optional[Section] = None
        if request.target_section_id is not None:
            section = await self.db.get(Section, request.target_section_id)
            if section is None or section.offering_id != offering.id:
                raise InvalidAdjustmentRequestError(
                    "target_section_id does not belong to this course's offering."
                )
        else:
            sections = (
                await self.db.execute(
                    select(Section).where(
                        Section.offering_id == offering.id,
                        Section.is_deleted == False,  # noqa: E712
                    )
                )
            ).scalars().all()
            for s in sections:
                if s.enrolled_count < s.capacity:
                    section = s
                    break
            if section is None:
                raise InvalidAdjustmentRequestError(
                    "All sections of this course are at capacity."
                )

        await self.agent.update_section_capacity(
            self.db, section.id, AddDropAction.ADD,
        )

        existing_link = (
            await self.db.execute(
                select(RegistrationCourse).where(
                    RegistrationCourse.registration_id == registration.id,
                    RegistrationCourse.course_id == request.course_id,
                )
            )
        ).scalar_one_or_none()
        if existing_link is None:
            self.db.add(RegistrationCourse(
                registration_id=registration.id,
                course_id=request.course_id,
                section_id=section.id,
                is_dropped=False,
            ))
        else:
            existing_link.section_id = section.id
            existing_link.is_dropped = False

    async def _apply_drop(
        self, request: AddDropRequest, registration: Registration,
    ) -> None:
        link = (
            await self.db.execute(
                select(RegistrationCourse).where(
                    RegistrationCourse.registration_id == registration.id,
                    RegistrationCourse.course_id == request.course_id,
                )
            )
        ).scalar_one_or_none()
        if link is None:
            raise InvalidAdjustmentRequestError(
                "Cannot drop a course the student is not registered for."
            )
        if link.section_id is not None:
            await self.agent.update_section_capacity(
                self.db, link.section_id, AddDropAction.DROP,
            )
        link.is_dropped = True

    # ── Officer override ────────────────────────────────────────

    async def officer_override(
        self,
        request_id: uuid.UUID,
        officer_role: UserRole,
        officer_id: uuid.UUID,
        justification: str,
    ) -> AddDropRequest:
        """
        Flip a DENIED request to OVERRIDDEN, then APPLIED. Officer
        role is REGISTRAR_OFFICER, DEPARTMENT_HEAD, or ADMIN —
        prerequisite-specific overrides go through a different audit
        trail (PrerequisiteOverride) handled by the registration
        service.
        """
        if officer_role not in {
            UserRole.REGISTRAR_OFFICER, UserRole.ADMIN,
        }:
            raise UnauthorizedActorError(
                "Only registrar officers or admins can override an add/drop request."
            )
        if not justification or not justification.strip():
            raise InvalidAdjustmentRequestError(
                "Override justification is required."
            )

        request = await self.requests.get(request_id)
        if request is None:
            raise EntityNotFoundError("AddDropRequest", str(request_id))
        if request.status != AddDropRequestStatus.DENIED:
            raise InvalidAdjustmentRequestError(
                f"Cannot override request in status {request.status.value}; "
                "only DENIED requests can be overridden."
            )

        registration = await self.registrations.get(request.registration_id)
        if registration is None:
            raise EntityNotFoundError(
                "Registration", str(request.registration_id),
            )

        request.status = AddDropRequestStatus.OVERRIDDEN
        request.override_by_id = officer_id
        request.override_justification = justification.strip()
        await self._apply_approved_change(request, registration)
        request.status = AddDropRequestStatus.APPLIED

        write_audit_log(
            action="course.add_drop.officer_override",
            actor_role=officer_role.value,
            actor_id=officer_id,
            resource_type="AddDropRequest",
            resource_id=request.id,
            decision=AddDropRequestStatus.APPLIED.value,
            metadata={
                "previous_reason": request.reason,
                "justification": justification.strip(),
            },
        )
        await self.db.commit()
        await self.db.refresh(request)
        return request

    # ── Reads ───────────────────────────────────────────────────

    async def get(self, request_id: uuid.UUID) -> Optional[AddDropRequest]:
        return await self.requests.get(request_id)

    async def list_for_registration(
        self, registration_id: uuid.UUID,
    ) -> list[AddDropRequest]:
        return await self.requests.list_for_registration(registration_id)
