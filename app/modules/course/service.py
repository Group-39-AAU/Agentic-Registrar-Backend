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
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from app.shared.email.service import EmailService

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger, write_audit_log
from app.modules.course.agents import (
    AcademicAdvisoryAgent, AcademicSchedulingAgent, Advice,
    CurriculumComplianceAgent, EnrollmentAdjustmentAgent,
)
from app.modules.course.exceptions import (
    AdjustmentDeniedError,
    ComplianceCheckFailedError,
    DuplicateRegistrationError,
    EntityNotFoundError,
    InvalidAdjustmentRequestError,
    InvalidStateTransitionError,
    RegistrationWindowClosedError,
    StudentAlreadyOnboardedError,
    UnauthorizedActorError,
)
from app.modules.course.models import (
    AcademicTerm, AddDropRequest, AdvisoryRecommendation,
    CourseManagementOfficer, Course, CourseOffering, Instructor,
    PrerequisiteOverride, Registration, RegistrationCourse,
    RegistrationStatusHistory, ScheduleConflict, Section, Student,
)
from app.modules.course.repository import (
    AcademicTermRepository, AddDropRequestRepository,
    AdvisoryRecommendationRepository, CourseRepository,
    RegistrationRepository, StudentRepository,
)
from app.shared.enums import (
    AddDropAction, AddDropRequestStatus, EnrollmentStatus, OfficerRole,
    RegistrationStatus, RiskStatus, SponsorshipType, UserRole,
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

        # 2. Resolve any prerequisite overrides for this registration so
        # the agent can skip prereq checks for the overridden courses
        # (SRS §3.5 Department-Head bypass).
        override_rows = (
            await self.db.execute(
                select(PrerequisiteOverride.course_id).where(
                    PrerequisiteOverride.registration_id == registration.id,
                )
            )
        ).scalars().all()
        overridden_course_ids = set(override_rows)

        # 3. Run the CurriculumComplianceAgent.
        compliance = await self.compliance_agent.process_task({
            "session": self.db,
            "registration": registration,
            "completed_course_ids": completed_course_ids or set(),
            "overridden_course_ids": overridden_course_ids,
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

    # ── Department-Head prerequisite override (SRS §3.5) ─────────

    async def grant_prerequisite_override(
        self,
        registration_id: uuid.UUID,
        course_id: uuid.UUID,
        officer_user_id: uuid.UUID,
        justification: str,
    ) -> PrerequisiteOverride:
        """
        Records a Department-Head bypass of the
        CurriculumComplianceAgent's prerequisite verdict.

        Authorisation: SRS §3.5 inverse requirement and SDS Table 62
        require role==DEPARTMENT_HEAD on the
        CourseManagementOfficer row — the auth-level User.role
        (REGISTRAR_OFFICER) is necessary but not sufficient. The
        check looks up the officer's CourseManagementOfficer row by
        user_id and rejects anything other than DEPARTMENT_HEAD.

        Idempotent on (registration_id, course_id) per the model's
        UniqueConstraint — a duplicate raises
        InvalidAdjustmentRequestError so the calling officer sees a
        clean reason rather than a 500.
        """
        if not justification or not justification.strip():
            raise InvalidAdjustmentRequestError(
                "Override justification is required."
            )

        # Officer-level check: must be a Department Head.
        officer = (
            await self.db.execute(
                select(CourseManagementOfficer).where(
                    CourseManagementOfficer.user_id == officer_user_id,
                    CourseManagementOfficer.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()
        if officer is None or officer.role != OfficerRole.DEPARTMENT_HEAD:
            raise UnauthorizedActorError(
                "Only a Department Head may grant a prerequisite "
                "override (SRS §3.5)."
            )

        # Resolve the registration + course so 404s are clean.
        registration = await self.registrations.get(registration_id)
        if registration is None:
            raise EntityNotFoundError("Registration", str(registration_id))
        course = await self.courses.get(course_id)
        if course is None:
            raise EntityNotFoundError("Course", str(course_id))

        # Idempotency: explicit pre-check so we surface a clean error.
        existing = (
            await self.db.execute(
                select(PrerequisiteOverride).where(
                    PrerequisiteOverride.registration_id == registration_id,
                    PrerequisiteOverride.course_id == course_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise InvalidAdjustmentRequestError(
                f"A prerequisite override already exists for course "
                f"{course.code} on this registration."
            )

        override = PrerequisiteOverride(
            registration_id=registration_id,
            course_id=course_id,
            granted_by_id=officer_user_id,
            justification=justification.strip(),
        )
        self.db.add(override)
        await self.db.flush()

        write_audit_log(
            action="course.prerequisite.override_granted",
            actor_role=UserRole.REGISTRAR_OFFICER.value,
            actor_id=officer_user_id,
            resource_type="Registration",
            resource_id=registration_id,
            decision="granted",
            metadata={
                "course_id": str(course_id),
                "course_code": course.code,
                "officer_role": OfficerRole.DEPARTMENT_HEAD.value,
                "justification_length": len(override.justification),
            },
        )
        await self.db.commit()
        await self.db.refresh(override)
        return override


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
        email_service: Optional["EmailService"] = None,
    ) -> None:
        self.db = db
        self.registrations = RegistrationRepository(db)
        self.requests = AddDropRequestRepository(db)
        self.agent = adjustment_agent or EnrollmentAdjustmentAgent()
        self.email_service = email_service

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
        await self._notify_student(registration, request)

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
        await self._notify_student(registration, request)
        await self.db.commit()
        await self.db.refresh(request)
        return request

    async def _notify_student(
        self, registration: Registration, request: AddDropRequest,
    ) -> None:
        """
        Wire the agent's notification payload to the EmailService.
        Always emits a structured stdout audit-log row; the actual
        email is only attempted when an EmailService was injected
        (production wiring) so unit tests run without SMTP setup.
        """
        payload = self.agent.notify_adjustment_success(
            registration.student_id, request,
        )
        write_audit_log(
            action="course.add_drop.notification",
            actor_role=UserRole.AGENT.value,
            actor_id=None,
            resource_type="AddDropRequest",
            resource_id=request.id,
            decision="notified",
            metadata=payload,
        )
        if self.email_service is None:
            return
        # Resolve the student's email via the User row.
        student = await self.db.get(Student, registration.student_id)
        if student is None:
            return
        from app.modules.auth.models import User
        user = await self.db.get(User, student.user_id)
        if user is None or not user.email:
            return
        from app.shared.email.schemas import EmailMessage
        await self.email_service.send(
            EmailMessage(
                to_email=user.email,
                subject=payload["subject"],
                text_body=payload["body"],
            )
        )

    # ── Reads ───────────────────────────────────────────────────

    async def get(self, request_id: uuid.UUID) -> Optional[AddDropRequest]:
        return await self.requests.get(request_id)

    async def list_for_registration(
        self, registration_id: uuid.UUID,
    ) -> list[AddDropRequest]:
        return await self.requests.list_for_registration(registration_id)


class AdvisoryService:
    """
    Persists AcademicAdvisoryAgent verdicts as AdvisoryRecommendation
    rows and exposes the HIGH-risk officer-review queue.

    Phase-1 contract: the calling service supplies ``cgpa`` and
    ``completed_course_ids`` because there is no Grade model yet
    (Track B). Once Track B lands, those inputs will be derived
    from the grade history.
    """

    def __init__(
        self,
        db: AsyncSession,
        *,
        advisory_agent: Optional[AcademicAdvisoryAgent] = None,
    ) -> None:
        self.db = db
        self.recommendations = AdvisoryRecommendationRepository(db)
        self.terms = AcademicTermRepository(db)
        self.registrations = RegistrationRepository(db)
        self.students = StudentRepository(db)
        self.agent = advisory_agent or AcademicAdvisoryAgent()

    # ── evaluate_plan ───────────────────────────────────────────

    async def evaluate_plan(
        self,
        student_id: uuid.UUID,
        term_id: uuid.UUID,
        proposed_course_ids: list[uuid.UUID],
        *,
        cgpa: float,
        completed_course_ids: Optional[set[uuid.UUID]] = None,
    ) -> AdvisoryRecommendation:
        """
        Run the AcademicAdvisoryAgent against the proposed plan,
        persist the verdict as an AdvisoryRecommendation row, and
        return it. HIGH-risk verdicts land with
        ``requires_officer_review=True`` so the officer queue picks
        them up immediately.
        """
        term = await self.terms.get(term_id)
        if term is None:
            raise EntityNotFoundError("AcademicTerm", str(term_id))

        student = (
            await self.db.execute(
                select(Student).where(Student.id == student_id)
            )
        ).scalar_one_or_none()
        if student is None:
            raise EntityNotFoundError("Student", str(student_id))

        # Resolve total proposed credits from the catalog.
        total_credits = 0
        if proposed_course_ids:
            rows = (
                await self.db.execute(
                    select(Course).where(
                        Course.id.in_(proposed_course_ids),
                        Course.is_deleted == False,  # noqa: E712
                    )
                )
            ).scalars().all()
            total_credits = sum(c.credit_hours for c in rows)

        # Phase-1 default: use the catalog department of the first
        # proposed course, falling back to "Computer Science" if
        # nothing is proposed yet.
        department = await self._resolve_department(student, proposed_course_ids)

        advice: Advice = await self.agent.process_task({
            "session": self.db,
            "department": department,
            "current_semester": student.current_semester,
            "cgpa": cgpa,
            "total_proposed_credits": total_credits,
            "completed_course_ids": completed_course_ids or set(),
        })

        recommendation = AdvisoryRecommendation(
            student_id=student_id,
            term_id=term_id,
            risk_status=advice.risk_status,
            risk_explanation=advice.explanation,
            proposed_courses=[str(cid) for cid in proposed_course_ids],
            recommended_courses=advice.recommended_courses,
            gap_analysis=advice.gap_analysis.to_dict(),
            requires_officer_review=advice.requires_officer_review,
        )
        self.db.add(recommendation)
        await self.db.flush()

        write_audit_log(
            action="course.advisory.evaluated",
            actor_role=UserRole.AGENT.value,
            actor_id=None,
            resource_type="AdvisoryRecommendation",
            resource_id=recommendation.id,
            decision=advice.risk_status.value,
            metadata={
                "agent_id": self.agent.agent_id,
                "requires_officer_review": advice.requires_officer_review,
                "total_proposed_credits": total_credits,
                "cgpa": cgpa,
            },
        )
        await self.db.commit()
        await self.db.refresh(recommendation)
        return recommendation

    async def _resolve_department(
        self, student: Student, proposed_course_ids: list[uuid.UUID],
    ) -> str:
        """
        Pick the department to evaluate against. The Phase-1
        Student entity carries no department field, so we read it
        from the first proposed course; if the student proposed
        nothing yet, fall back to the first known department in
        the catalog so the gap analysis still has something to
        ground in.
        """
        if proposed_course_ids:
            first_course = (
                await self.db.execute(
                    select(Course).where(Course.id == proposed_course_ids[0])
                )
            ).scalar_one_or_none()
            if first_course is not None:
                return first_course.department
        any_course = (
            await self.db.execute(
                select(Course).where(
                    Course.is_deleted == False  # noqa: E712
                ).limit(1)
            )
        ).scalar_one_or_none()
        return any_course.department if any_course else "Unknown"

    # ── Officer review queue ────────────────────────────────────

    async def list_high_risk_open(
        self,
        term_id: uuid.UUID,
        officer_role: UserRole,
    ) -> list[AdvisoryRecommendation]:
        if officer_role not in {
            UserRole.REGISTRAR_OFFICER, UserRole.ADMIN,
        }:
            raise UnauthorizedActorError(
                "Only registrar officers or admins can view the "
                "advisory officer-review queue."
            )
        return await self.recommendations.list_high_risk_open(term_id)

    async def close_officer_review(
        self,
        recommendation_id: uuid.UUID,
        officer_role: UserRole,
        officer_id: uuid.UUID,
        review_notes: str,
    ) -> AdvisoryRecommendation:
        if officer_role not in {
            UserRole.REGISTRAR_OFFICER, UserRole.ADMIN,
        }:
            raise UnauthorizedActorError(
                "Only registrar officers or admins can close an "
                "advisory review."
            )
        if not review_notes or not review_notes.strip():
            raise InvalidAdjustmentRequestError(
                "Review notes are required to close an advisory review."
            )
        recommendation = await self.recommendations.get(recommendation_id)
        if recommendation is None:
            raise EntityNotFoundError(
                "AdvisoryRecommendation", str(recommendation_id),
            )
        if recommendation.reviewed_at is not None:
            raise InvalidAdjustmentRequestError(
                "Advisory recommendation has already been reviewed."
            )
        recommendation.reviewed_by_id = officer_id
        recommendation.reviewed_at = datetime.now(timezone.utc)
        recommendation.review_notes = review_notes.strip()

        write_audit_log(
            action="course.advisory.review_closed",
            actor_role=officer_role.value,
            actor_id=officer_id,
            resource_type="AdvisoryRecommendation",
            resource_id=recommendation.id,
            decision="reviewed",
            metadata={
                "risk_status": recommendation.risk_status.value,
                "notes_length": len(recommendation.review_notes or ""),
            },
        )
        await self.db.commit()
        await self.db.refresh(recommendation)
        return recommendation

    # ── Reads ───────────────────────────────────────────────────

    async def get(
        self, recommendation_id: uuid.UUID,
    ) -> Optional[AdvisoryRecommendation]:
        return await self.recommendations.get(recommendation_id)

    async def list_for_student(
        self, student_id: uuid.UUID,
    ) -> list[AdvisoryRecommendation]:
        return await self.recommendations.list_for_student(student_id)


class OnboardingService:
    """
    Bridges the undergraduate admission module's Enrollment row to a
    course-management Student row. Without this, an admitted student
    has a User account + an Enrollment record but no Student profile,
    so they cannot use any of the Track A endpoints.

    Phase-1 contract: officer-triggered (REGISTRAR_OFFICER or ADMIN)
    via POST /api/v1/courses/officer/students/onboard-from-enrollment.
    Phase-2 will replace this with an event-bus subscription on an
    EnrollmentCompletedEvent published by the admission Enrollment Agent.

    Idempotent: re-running for an Enrollment whose Student already
    exists raises StudentAlreadyOnboardedError carrying the existing
    student_id, which the router maps to 409.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.students = StudentRepository(db)

    async def onboard_student_from_enrollment(
        self,
        enrollment_id: uuid.UUID,
        officer_role: UserRole,
        officer_id: uuid.UUID,
    ) -> Student:
        if officer_role not in {UserRole.REGISTRAR_OFFICER, UserRole.ADMIN}:
            raise UnauthorizedActorError(
                "Only registrar officers or admins can onboard a student "
                "from an Enrollment record."
            )

        # Resolve the Enrollment row.
        from app.modules.undergraduate.enrollment.models import Enrollment
        enrollment = (
            await self.db.execute(
                select(Enrollment).where(Enrollment.id == enrollment_id)
            )
        ).scalar_one_or_none()
        if enrollment is None:
            raise EntityNotFoundError("Enrollment", str(enrollment_id))

        # Idempotency: if a Student already exists for this user, surface
        # it as a conflict rather than creating a duplicate.
        existing = await self.students.get_by_user_id(enrollment.applicant_id)
        if existing is not None:
            raise StudentAlreadyOnboardedError(existing.student_id)

        # Pull display name from the User row.
        from app.modules.auth.models import User
        user = await self.db.get(User, enrollment.applicant_id)
        if user is None:
            raise EntityNotFoundError("User", str(enrollment.applicant_id))
        full_name = f"{user.first_name} {user.last_name}".strip() or user.email

        student = Student(
            user_id=enrollment.applicant_id,
            student_id=enrollment.university_id,
            full_name=full_name,
            current_semester=1,                    # fresh admit
            enrollment_status=EnrollmentStatus.ACTIVE,
        )
        self.db.add(student)
        await self.db.flush()

        write_audit_log(
            action="course.student.onboarded_from_enrollment",
            actor_role=officer_role.value,
            actor_id=officer_id,
            resource_type="Student",
            resource_id=student.id,
            decision="created",
            metadata={
                "enrollment_id": str(enrollment_id),
                "university_id": enrollment.university_id,
                "department": enrollment.department,
                "section": enrollment.section,
            },
        )
        await self.db.commit()
        await self.db.refresh(student)
        return student
