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
    TermNotYetOpenError,
    UnauthorizedActorError,
)
from app.modules.course.models import (
    AcademicTerm, AddDropRequest, AdvisoryRecommendation,
    ClassScheduleSlot, Classroom, CourseManagementOfficer, Course,
    Instructor, InstructorAssignment, PrerequisiteOverride, Registration,
    RegistrationCourse, RegistrationStatusHistory, ScheduleConflict, Section,
    Student,
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
        RegistrationStatus.REGISTRATION_OPEN,    # bursar callback cleared the hold
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

    async def list_terms(
        self, *, is_open: Optional[bool] = None,
    ) -> list[AcademicTerm]:
        """
        Catalog read of every academic term. No role gate — officers,
        instructors, and students all need a term picker. Optional
        ``is_open`` narrows the result to only open / only closed terms.
        """
        return await self.terms.list_all(is_open=is_open)

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
        Return the courses the calling student can register for in
        the current term. The filter is strict:

            course.department == student.department
            AND course.semester == student.current_semester

        Each engineering department owns its own version of every
        foundational course (its own Calculus I, its own Discrete
        Math, etc.), so a Software Engineering student in semester 3
        only sees the four SE-tagged semester-3 courses — never a CE
        or BME course at the same level.

        If the student row predates the Student.department migration
        and was never reconciled, the filter falls back to the
        semester-only view so we don't show an empty curriculum.
        """
        student = (
            await self.db.execute(
                select(Student).where(Student.id == student_id)
            )
        ).scalar_one_or_none()
        if student is None:
            raise EntityNotFoundError("Student", str(student_id))

        filters = [
            Course.semester == student.current_semester,
            Course.is_deleted == False,  # noqa: E712
        ]
        if student.department is not None:
            filters.append(Course.department == student.department)

        return list(
            (
                await self.db.execute(
                    select(Course)
                    .where(*filters)
                    .order_by(Course.code.asc())
                )
            ).scalars().all()
        )

    async def list_available_courses(
        self, student_id: uuid.UUID, term_id: uuid.UUID,
    ) -> dict:
        """
        Three-rule resolution keyed off the term's open flag and the
        term's dates relative to today:

          1. Term is OPEN → return the curriculum picker the student
             would register from. The semester is computed via
             :meth:`_semester_for_term` so the list matches *this
             term's* expected semester, not just ``student.current_semester``.
             When the computed semester is outside ``[1, 10]`` the
             curriculum list is empty.

          2. Term is CLOSED and has already started (``today >=
             start_date``) → treat as past/in-progress. Look up the
             student's Registration for the term:
               * Found → return the active (non-dropped) registered
                 courses.
               * Not found → raise ``EntityNotFoundError`` for a
                 Registration so the router surfaces "you didn't
                 register for this term" as a 404.

          3. Term is CLOSED and has not started yet (``today <
             start_date``) → raise :class:`TermNotYetOpenError` so
             the router can surface a 409 "this term is not open yet".

        Also raises ``EntityNotFoundError`` if either the Student or
        the AcademicTerm itself is missing.
        """
        student = (
            await self.db.execute(
                select(Student).where(Student.id == student_id)
            )
        ).scalar_one_or_none()
        if student is None:
            raise EntityNotFoundError("Student", str(student_id))

        term = (
            await self.db.execute(
                select(AcademicTerm).where(
                    AcademicTerm.id == term_id,
                    AcademicTerm.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()
        if term is None:
            raise EntityNotFoundError("AcademicTerm", str(term_id))

        today = date.today()

        # Rule 1 — open term: curriculum picker, per-term semester.
        if term.is_open:
            target_semester = await self._semester_for_term(student, term)
            if target_semester is None or not (1 <= target_semester <= 10):
                courses: list[Course] = []
            else:
                filters = [
                    Course.semester == target_semester,
                    Course.is_deleted == False,  # noqa: E712
                ]
                if student.department is not None:
                    filters.append(Course.department == student.department)
                courses = list(
                    (
                        await self.db.execute(
                            select(Course)
                            .where(*filters)
                            .order_by(Course.code.asc())
                        )
                    ).scalars().all()
                )
            return {
                "term": term,
                "is_registered": False,
                "registration_id": None,
                "registration_status": None,
                "courses": courses,
            }

        # Rule 3 — closed, future: not open yet.
        if today < term.start_date:
            raise TermNotYetOpenError(term.term_name)

        # Rule 2 — closed, past/in-progress: must have a registration.
        registration = (
            await self.db.execute(
                select(Registration).where(
                    Registration.student_id == student_id,
                    Registration.term_id == term.id,
                    Registration.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()
        if registration is None:
            raise EntityNotFoundError(
                "Registration",
                f"student={student.student_id}, term='{term.term_name}'",
            )

        rows = (
            await self.db.execute(
                select(Course)
                .join(
                    RegistrationCourse,
                    RegistrationCourse.course_id == Course.id,
                )
                .where(
                    RegistrationCourse.registration_id == registration.id,
                    RegistrationCourse.is_dropped == False,  # noqa: E712
                    Course.is_deleted == False,  # noqa: E712
                )
                .order_by(Course.code.asc())
            )
        ).scalars().all()
        return {
            "term": term,
            "is_registered": True,
            "registration_id": registration.id,
            "registration_status": registration.status,
            "courses": list(rows),
        }

    async def _semester_for_term(
        self, student: Student, target_term: AcademicTerm,
    ) -> Optional[int]:
        """
        Compute the semester this student is in for ``target_term``.

        Anchor: the currently-open AcademicTerm pairs with
        ``student.current_semester``. Every chronological step away
        from that anchor (ordered by ``start_date``) shifts the
        semester by ±1. Returns ``None`` when no term is open —
        callers fall back to whatever they consider sensible.
        """
        terms = list(
            (
                await self.db.execute(
                    select(AcademicTerm)
                    .where(AcademicTerm.is_deleted == False)  # noqa: E712
                    .order_by(AcademicTerm.start_date.asc())
                )
            ).scalars().all()
        )
        anchor = next((t for t in terms if t.is_open), None)
        if anchor is None:
            return None
        try:
            anchor_idx = next(
                i for i, t in enumerate(terms) if t.id == anchor.id
            )
            target_idx = next(
                i for i, t in enumerate(terms) if t.id == target_term.id
            )
        except StopIteration:
            return None
        return student.current_semester + (target_idx - anchor_idx)

    # ── Student dashboard read ───────────────────────────────────

    async def get_student_dashboard(
        self, student_id: uuid.UUID,
    ) -> dict:
        """
        Consolidated read for the student's portal home: identity
        attributes (name, UGR id, department, semester, sponsorship)
        plus the current-term context (open AcademicTerm + the cohort
        Section the student is allocated to, if scheduling has run).

        ``current_term`` is null when no term is currently open.
        Inside ``current_term``, ``section`` is null when the student
        has not yet been allocated to a cohort (officer hasn't run
        scheduling, or the student didn't register for that term).
        """
        student = (
            await self.db.execute(
                select(Student).where(Student.id == student_id)
            )
        ).scalar_one_or_none()
        if student is None:
            raise EntityNotFoundError("Student", str(student_id))

        # Pull email + display name from the User row — Student.full_name
        # is a denormalised display string; the auth identity lives on
        # User.
        from app.modules.auth.models import User
        user = await self.db.get(User, student.user_id)

        current_term_payload = None
        open_term = (
            await self.db.execute(
                select(AcademicTerm).where(
                    AcademicTerm.is_open == True,    # noqa: E712
                ).order_by(AcademicTerm.start_date.asc())
            )
        ).scalars().first()

        if open_term is not None:
            registration = (
                await self.db.execute(
                    select(Registration).where(
                        Registration.student_id == student_id,
                        Registration.term_id == open_term.id,
                        Registration.is_deleted == False,  # noqa: E712
                    )
                )
            ).scalar_one_or_none()

            section_payload = None
            if registration is not None and registration.section_id is not None:
                section = await self.db.get(Section, registration.section_id)
                if section is not None and not section.is_deleted:
                    section_payload = {
                        "section_id": section.id,
                        "section_code": section.section_code,
                        "capacity": section.capacity,
                        "enrolled_count": section.enrolled_count,
                    }

            current_term_payload = {
                "term_id": open_term.id,
                "term_name": open_term.term_name,
                "start_date": open_term.start_date,
                "end_date": open_term.end_date,
                "registration_status": (
                    registration.status if registration else None
                ),
                "section": section_payload,
            }

        return {
            "student_id": student.student_id,
            "full_name": student.full_name,
            "email": user.email if user else None,
            "department": student.department,
            "current_semester": student.current_semester,
            "sponsorship_type": student.sponsorship_type,
            "enrollment_status": student.enrollment_status,
            "current_term": current_term_payload,
        }

    # ── Draft management ─────────────────────────────────────────

    async def create_draft(
        self,
        student_id: uuid.UUID,
        term_id: uuid.UUID,
        sponsorship_type: Optional[SponsorshipType] = None,
    ) -> Registration:
        """
        Create a draft registration. ``sponsorship_type`` is normally
        inherited from ``Student.sponsorship_type`` (set at onboarding
        from the admission record) — callers shouldn't supply it. The
        explicit override stays for tests / officer-driven flows.
        """
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

        if sponsorship_type is None:
            student = (
                await self.db.execute(
                    select(Student).where(Student.id == student_id)
                )
            ).scalar_one_or_none()
            if student is None:
                raise EntityNotFoundError("Student", str(student_id))
            if student.sponsorship_type is None:
                raise EntityNotFoundError(
                    "Student.sponsorship_type",
                    f"missing for student {student_id} — onboarding "
                    "should have set it from the admission record",
                )
            sponsorship_type = student.sponsorship_type

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

    # ── Tuition invoice ──────────────────────────────────────────

    async def get_invoice(
        self,
        registration_id: uuid.UUID,
        student_user_id: uuid.UUID,
    ) -> dict:
        """
        Per-credit-hour tuition breakdown for a registration. Each
        non-dropped course contributes ``credit_hours *
        FEE_PER_CREDIT_HOUR_BIRR`` birr to the line items; the total
        is the sum.

        Sponsorship branching:
          - SELF_SPONSORED → ``amount_due`` is the full sum and
            ``payment_required=True``. The student must clear via
            /payment/initiate + /payment/callback.
          - GOVERNMENT → the same line items are returned for record-
            keeping, but ``amount_due=0`` and ``payment_required=False``.
            The student covers their load via /cost-sharing-form.

        Reachable in any registration status (the student wants to
        see the bill from draft onward, and after REGISTERED for
        their records).
        """
        from app.core.config import settings as _settings

        registration = await self.registrations.get(registration_id)
        if registration is None:
            raise EntityNotFoundError("Registration", str(registration_id))
        await self._assert_owner(registration, student_user_id)

        await self.db.refresh(registration, attribute_names=["courses"])
        active_course_ids = [
            rc.course_id for rc in registration.courses if not rc.is_dropped
        ]
        courses: list[Course] = []
        if active_course_ids:
            courses = list(
                (
                    await self.db.execute(
                        select(Course)
                        .where(Course.id.in_(active_course_ids))
                        .order_by(Course.code.asc())
                    )
                ).scalars().all()
            )

        rate = _settings.FEE_PER_CREDIT_HOUR_BIRR
        currency = _settings.TUITION_CURRENCY

        lines = [
            {
                "course_id": c.id,
                "course_code": c.code,
                "course_title": c.title,
                "credit_hours": c.credit_hours,
                "line_total": c.credit_hours * rate,
            }
            for c in courses
        ]
        total_credit_hours = sum(c.credit_hours for c in courses)
        gross_total = total_credit_hours * rate

        is_government = (
            registration.sponsorship_type == SponsorshipType.GOVERNMENT
        )
        amount_due = 0 if is_government else gross_total
        if not active_course_ids:
            note = (
                "This registration has no active courses, so there is "
                "nothing to bill yet. Add courses and re-fetch."
            )
        elif is_government:
            note = (
                "Cost-sharing covers this load — submit the cost-"
                "sharing form to acknowledge."
            )
        else:
            note = (
                f"Self-sponsored: {total_credit_hours} credit hours × "
                f"{rate} {currency}/credit-hour = {gross_total} "
                f"{currency} due. Use /payment/initiate to settle."
            )

        return {
            "registration_id": registration.id,
            "sponsorship_type": registration.sponsorship_type,
            "currency": currency,
            "fee_per_credit_hour": rate,
            "lines": lines,
            "total_credit_hours": total_credit_hours,
            "gross_total": gross_total,
            "amount_due": amount_due,
            "is_government_sponsored": is_government,
            "payment_required": (not is_government) and bool(active_course_ids),
            "note": note,
        }

    # ── Mock payment (mirrors the admission module's pattern) ────
    #
    # Two-step shape, identical to /undergraduate/applications/{id}/payment/*:
    #   1. Student calls /initiate to mint a payment_reference.
    #   2. The "gateway" (us, manually, until a real bursar is wired up)
    #      calls /callback with that reference. The callback marks every
    #      course in the draft as paid in the in-memory PayMock so the
    #      compliance agent will let /submit through.

    _PAYMENT_INITIATE_STATES = {
        RegistrationStatus.REGISTRATION_OPEN,
        RegistrationStatus.ADVISOR_REVIEW,
        RegistrationStatus.PAYMENT_HOLD,         # retry after a /submit bounce
    }

    async def initiate_payment(
        self,
        registration_id: uuid.UUID,
        student_user_id: uuid.UUID,
    ) -> Registration:
        """
        Generate a simulated payment reference for a registration that
        the caller owns. Allowed from REGISTRATION_OPEN, ADVISOR_REVIEW,
        or PAYMENT_HOLD (so a student can retry after a bounce).

        Idempotent: if a reference already exists on the row it's
        returned unchanged.
        """
        registration = await self.registrations.get(registration_id)
        if registration is None:
            raise EntityNotFoundError("Registration", str(registration_id))
        if registration.status not in self._PAYMENT_INITIATE_STATES:
            raise InvalidStateTransitionError(
                registration.status.value, "initiate payment",
            )
        await self._assert_owner(registration, student_user_id)

        if not registration.payment_reference:
            registration.payment_reference = (
                f"COURSE-PAY-{uuid.uuid4().hex[:12].upper()}"
            )
            await self.db.commit()
            await self.db.refresh(registration, attribute_names=["courses"])
        return registration

    async def complete_payment(
        self,
        registration_id: uuid.UUID,
        payment_reference: str,
    ) -> Registration:
        """
        Simulated bursar-gateway callback. Validates the reference,
        then marks every course in the draft as paid in pay_mock so
        ``/submit`` can clear the payment check.
        """
        registration = await self.registrations.get(registration_id)
        if registration is None:
            raise EntityNotFoundError("Registration", str(registration_id))
        if registration.status not in {
            RegistrationStatus.REGISTRATION_OPEN,
            RegistrationStatus.ADVISOR_REVIEW,
            RegistrationStatus.PAYMENT_HOLD,
        }:
            raise InvalidStateTransitionError(
                registration.status.value, "payment callback",
            )
        if not registration.payment_reference:
            raise InvalidAdjustmentRequestError(
                "No payment_reference on this registration; call "
                "/payment/initiate before the gateway callback."
            )
        if registration.payment_reference != payment_reference:
            raise InvalidAdjustmentRequestError(
                "Payment reference does not match the registration."
            )

        from app.modules.course.services import pay_mock as _pay_mock
        await self.db.refresh(registration, attribute_names=["courses"])
        marked = 0
        for rc in registration.courses:
            if rc.is_dropped:
                continue
            _pay_mock.set_payment_status(
                registration.student_id, rc.course_id, paid=True,
            )
            marked += 1

        # If the registration was sitting in PAYMENT_HOLD waiting for
        # the gateway, clear the hold so /submit can run again. For
        # drafts that haven't been submitted yet, leave the status
        # alone — payment can be initiated and completed before the
        # first /submit call.
        if registration.status == RegistrationStatus.PAYMENT_HOLD:
            await self._transition(
                registration,
                RegistrationStatus.REGISTRATION_OPEN,
                changed_by_id=None,
                reason="Payment gateway callback cleared the hold",
            )

        write_audit_log(
            action="course.registration.payment_completed",
            actor_role=UserRole.STUDENT.value,
            actor_id=None,
            resource_type="Registration",
            resource_id=registration.id,
            decision="paid",
            metadata={
                "payment_reference": payment_reference,
                "courses_marked_paid": marked,
            },
        )
        await self.db.commit()
        await self.db.refresh(registration, attribute_names=["courses"])
        return registration

    # ── Government cost-sharing form ─────────────────────────────
    #
    # Government-sponsored students don't pay per-course; the
    # university bursar's office accepts a single "cost-sharing form"
    # signed by the student that covers every registered course in the
    # term. Submitting the form is treated as the equivalent of the
    # bursar callback for self-sponsored students: every non-dropped
    # course on the registration gets pay_mock.set_payment_status(...,
    # paid=True), and a PAYMENT_HOLD registration is moved back to
    # REGISTRATION_OPEN so /submit can clear.

    _COST_SHARING_STATES = {
        RegistrationStatus.REGISTRATION_OPEN,
        RegistrationStatus.ADVISOR_REVIEW,
        RegistrationStatus.PAYMENT_HOLD,
    }

    async def submit_cost_sharing_form(
        self,
        registration_id: uuid.UUID,
        student_user_id: uuid.UUID,
    ) -> dict:
        """
        Materialise a cost-sharing form for a GOVERNMENT-sponsored
        registration. Idempotent on re-runs (re-marking already-paid
        courses is a no-op).

        Raises
        ------
        EntityNotFoundError
            The registration does not exist.
        UnauthorizedActorError
            The caller is not the owner of the registration.
        InvalidAdjustmentRequestError
            The registration is self-sponsored (form is gov-only) or
            it has no active courses to cover.
        InvalidStateTransitionError
            The registration is past the point where payment status
            can still be settled (REGISTERED / ADD_DROP_WINDOW /
            CANCELLED).
        """
        registration = await self.registrations.get(registration_id)
        if registration is None:
            raise EntityNotFoundError("Registration", str(registration_id))
        await self._assert_owner(registration, student_user_id)

        if registration.sponsorship_type != SponsorshipType.GOVERNMENT:
            raise InvalidAdjustmentRequestError(
                "Cost-sharing form is only available for government-"
                "sponsored registrations. Self-sponsored students must "
                "complete payment via /payment/initiate + /payment/callback."
            )
        if registration.status not in self._COST_SHARING_STATES:
            raise InvalidStateTransitionError(
                registration.status.value, "submit cost-sharing form",
            )

        await self.db.refresh(registration, attribute_names=["courses"])
        active_course_ids = [
            rc.course_id for rc in registration.courses if not rc.is_dropped
        ]
        if not active_course_ids:
            raise InvalidAdjustmentRequestError(
                "Cannot submit a cost-sharing form for a registration "
                "with no active courses."
            )

        from app.modules.course.services import pay_mock as _pay_mock
        for cid in active_course_ids:
            _pay_mock.set_payment_status(
                registration.student_id, cid, paid=True,
            )

        # If the registration was bouncing on PAYMENT_HOLD, the form
        # clears it back to REGISTRATION_OPEN so the student can
        # re-submit. Drafts that haven't been submitted yet stay put.
        if registration.status == RegistrationStatus.PAYMENT_HOLD:
            await self._transition(
                registration,
                RegistrationStatus.REGISTRATION_OPEN,
                changed_by_id=None,
                reason="Cost-sharing form cleared the payment hold",
            )

        write_audit_log(
            action="course.registration.cost_sharing_form_submitted",
            actor_role=UserRole.STUDENT.value,
            actor_id=None,
            resource_type="Registration",
            resource_id=registration.id,
            decision="cost_sharing_acknowledged",
            metadata={
                "course_count": len(active_course_ids),
                "marked_paid_course_ids": [str(c) for c in active_course_ids],
            },
        )
        await self.db.commit()
        await self.db.refresh(registration, attribute_names=["courses"])
        return {
            "registration_id": str(registration.id),
            "sponsorship_type": registration.sponsorship_type.value,
            "course_count": len(active_course_ids),
            "marked_paid_course_ids": [str(c) for c in active_course_ids],
        }

    async def _assert_owner(
        self, registration: Registration, student_user_id: uuid.UUID,
    ) -> None:
        """Raise UnauthorizedActorError if the registration is not the caller's."""
        student = (
            await self.db.execute(
                select(Student).where(Student.id == registration.student_id)
            )
        ).scalar_one_or_none()
        if student is None or student.user_id != student_user_id:
            raise UnauthorizedActorError(
                "This registration does not belong to the calling user."
            )

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

    # ── One-shot register (collapses draft + add + remove + submit) ─

    async def select_courses_and_submit(
        self,
        student_id: uuid.UUID,
        student_user_id: uuid.UUID,
        term_id: uuid.UUID,
        course_ids: list[uuid.UUID],
        completed_course_ids: Optional[set[uuid.UUID]] = None,
    ) -> tuple[Registration, dict]:
        """
        Collapses the four-step registration flow (create draft → add
        course → remove course → submit) into a single call. Used by
        ``POST /api/v1/courses/me/register``.

        The student supplies the target term and the exact list of
        course ids they want to take. The service:

          1. Finds the student's existing Registration for the term,
             or creates a fresh draft.
          2. Reconciles ``RegistrationCourse`` rows so the registration
             holds exactly the requested course set: adds the missing
             ones, deletes the extras.
          3. Runs :meth:`submit` so the registration goes through the
             usual prereq + load + payment compliance pipeline.

        Idempotent on the happy path while the registration is in
        ``REGISTRATION_OPEN``: a retry with the same ``course_ids``
        re-runs submit without churning the join table. A retry with
        a different list reconciles first.

        Raises
        ------
        InvalidStateTransitionError
            The registration already finalised (``REGISTERED``,
            ``ADD_DROP_WINDOW``), or sits in ``PAYMENT_HOLD`` /
            ``CHECKING_*`` and the student must clear the hold before
            calling this endpoint.
        EntityNotFoundError
            One of the supplied ``course_ids`` does not exist.
        RegistrationWindowClosedError
            The term's registration window is closed (raised by
            ``create_draft`` when no existing registration exists).
        ComplianceCheckFailedError
            Bubbled up from :meth:`submit` when the agent rejects the
            final list (prereq miss, load over ceiling, unpaid).
        """
        existing = (
            await self.db.execute(
                select(Registration).where(
                    Registration.student_id == student_id,
                    Registration.term_id == term_id,
                    Registration.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()

        if existing is None:
            # No prior registration in this term — create a fresh draft.
            # create_draft is the canonical place that checks the term
            # window is open, so we don't duplicate that guard here.
            registration = await self.create_draft(student_id, term_id)
        else:
            registration = existing

        # The reconcile step only makes sense while the draft is
        # mutable — otherwise courses are locked. Allow the caller to
        # retry while in REGISTRATION_OPEN (post-bounce or fresh) but
        # block PAYMENT_HOLD / REGISTERED / CHECKING_* / CANCELLED so
        # the student can't sneak around the state machine.
        if registration.status != RegistrationStatus.REGISTRATION_OPEN:
            raise InvalidStateTransitionError(
                registration.status.value,
                "select-courses-and-submit (registration must be in REGISTRATION_OPEN)",
            )

        # Validate every course id up front so we don't half-mutate.
        for cid in course_ids:
            course = await self.courses.get(cid)
            if course is None:
                raise EntityNotFoundError("Course", str(cid))

        # Reconcile to the target set.
        await self.db.refresh(registration, attribute_names=["courses"])
        target = set(course_ids)
        current_links = {rc.course_id: rc for rc in registration.courses}
        current = set(current_links.keys())

        # Delete extras
        for cid in current - target:
            await self.db.delete(current_links[cid])
        # Add missing
        for cid in target - current:
            self.db.add(RegistrationCourse(
                registration_id=registration.id,
                course_id=cid,
            ))

        await self.db.flush()

        # Hand off to submit() — the compliance pipeline + state
        # transitions + audit trail all happen there.
        return await self.submit(
            registration.id,
            student_user_id=student_user_id,
            completed_course_ids=completed_course_ids,
        )

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

    # ── Phase 1: section allocation ─────────────────────────────

    async def allocate_sections(
        self,
        term_id: uuid.UUID,
        department: str,
        officer_role: UserRole,
        officer_id: uuid.UUID,
    ) -> dict:
        """
        Run cohort allocation only (no schedule slots yet) for a
        single (term, department) pair. Officer-only.

        After this returns, every REGISTERED student in the
        department has a ``Registration.section_id`` set. The
        per-class meetings (``ClassScheduleSlot`` rows) come from a
        separate :meth:`generate_timetable` call so the officer can
        review the allocation before locking in the schedule.
        """
        self._require_officer(officer_role)
        term = await self.terms.get(term_id)
        if term is None:
            raise EntityNotFoundError("AcademicTerm", str(term_id))

        # Fail fast on missing classroom inventory: without rooms the
        # agent can't place anyone, and silently returning an empty
        # allocation hides the configuration gap from the officer.
        classroom_count = (
            await self.db.execute(
                select(Classroom.id).where(
                    Classroom.department == department,
                    Classroom.is_deleted == False,  # noqa: E712
                )
            )
        ).first()
        if classroom_count is None:
            raise InvalidAdjustmentRequestError(
                f"No classrooms are registered for department '{department}'. "
                "Add at least one Classroom before allocating sections."
            )

        allocation = await self.scheduling_agent.allocate_sections(
            self.db, term_id, department,
        )
        await self.db.commit()

        write_audit_log(
            action="course.sections.allocated",
            actor_role=officer_role.value,
            actor_id=officer_id,
            resource_type="AcademicTerm",
            resource_id=term_id,
            decision="ok",
            metadata={
                "department": department,
                "students_placed": len(allocation.students_placed),
                "sections_created": len(allocation.sections_created),
                "failed": len(allocation.failed),
            },
        )
        return {
            "term_id": str(term_id),
            "department": department,
            "sections_created": allocation.sections_created,
            "students_placed_count": len(allocation.students_placed),
            "students_placed": allocation.students_placed,
            "failed": allocation.failed,
        }

    # ── Phase 2: weekly-timetable generation ────────────────────

    async def generate_timetable(
        self,
        term_id: uuid.UUID,
        department: str,
        officer_role: UserRole,
        officer_id: uuid.UUID,
    ) -> dict:
        """
        Build the per-section weekly schedule (``ClassScheduleSlot``
        rows) for every Section the department already has. Requires
        :meth:`allocate_sections` to have run first — if no sections
        exist for this (term, department), the response will be
        empty.

        Idempotent: re-runs delete the department's existing slots
        and rebuild from scratch.
        """
        self._require_officer(officer_role)
        term = await self.terms.get(term_id)
        if term is None:
            raise EntityNotFoundError("AcademicTerm", str(term_id))

        # Refuse to run before any sections exist — otherwise the
        # officer would silently get an empty timetable response and
        # think the run had succeeded.
        section_exists = (
            await self.db.execute(
                select(Section.id).where(
                    Section.term_id == term_id,
                    Section.department == department,
                    Section.is_deleted == False,  # noqa: E712
                )
            )
        ).first()
        if section_exists is None:
            raise InvalidAdjustmentRequestError(
                f"No sections exist for department '{department}' in this term. "
                "Call POST /officer/sections/allocate first."
            )

        artefact = await self.scheduling_agent.generate_schedule(
            self.db, term_id, department,
        )
        await self.db.commit()

        write_audit_log(
            action="course.timetable.generated",
            actor_role=officer_role.value,
            actor_id=officer_id,
            resource_type="AcademicTerm",
            resource_id=term_id,
            decision="ok",
            metadata={
                "department": department,
                "section_count": artefact.section_count,
                "slots_created": artefact.slots_created,
                "conflicts": len(artefact.conflict_ids),
            },
        )
        return {
            "term_id": str(term_id),
            "department": department,
            "section_count": artefact.section_count,
            "slots_created": artefact.slots_created,
            "sections": artefact.sections,
            "conflict_count": len(artefact.conflict_ids),
            "conflict_ids": [str(cid) for cid in artefact.conflict_ids],
        }

    def _require_officer(self, officer_role: UserRole) -> None:
        if officer_role not in {UserRole.REGISTRAR_OFFICER, UserRole.ADMIN}:
            raise UnauthorizedActorError(
                "Only registrar officers or admins can run scheduling."
            )

    # ── Read views ───────────────────────────────────────────────

    async def get_student_schedule(
        self,
        student_id: uuid.UUID,
        term_id: uuid.UUID,
    ) -> dict:
        """
        Schedule for a single student: resolves the student's
        Registration for the term, finds its Section, and returns
        every ClassScheduleSlot for that section. Empty
        ``slots`` ⇒ the student has not been allocated yet (officer
        hasn't run scheduling) or has no registration in this term.
        """
        registration = (
            await self.db.execute(
                select(Registration).where(
                    Registration.student_id == student_id,
                    Registration.term_id == term_id,
                    Registration.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()

        if registration is None or registration.section_id is None:
            return {
                "term_id": str(term_id),
                "student_id": str(student_id),
                "section": None,
                "slots": [],
            }

        return await self._section_schedule_payload(
            section_id=registration.section_id,
            term_id=term_id,
            student_id=student_id,
        )

    async def get_section_schedule(
        self,
        section_id: uuid.UUID,
    ) -> dict:
        """
        Schedule for a Section regardless of who's asking. Used by
        ``GET /me/schedule`` (after resolving the caller's section)
        and ``GET /sections/{id}/schedule``.
        """
        return await self._section_schedule_payload(
            section_id=section_id, term_id=None, student_id=None,
        )

    async def _section_schedule_payload(
        self,
        *,
        section_id: uuid.UUID,
        term_id: Optional[uuid.UUID],
        student_id: Optional[uuid.UUID],
    ) -> dict:
        section = (
            await self.db.execute(
                select(Section).where(
                    Section.id == section_id,
                    Section.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()
        if section is None:
            raise EntityNotFoundError("Section", str(section_id))

        slot_rows = (
            await self.db.execute(
                select(ClassScheduleSlot, Course).join(
                    Course, Course.id == ClassScheduleSlot.course_id,
                ).where(
                    ClassScheduleSlot.section_id == section_id,
                ).order_by(
                    ClassScheduleSlot.day_of_week.asc(),
                    ClassScheduleSlot.start_time.asc(),
                )
            )
        ).all()

        return {
            "term_id": str(term_id) if term_id else str(section.term_id),
            "student_id": str(student_id) if student_id else None,
            "section": {
                "section_id": str(section.id),
                "section_code": section.section_code,
                "department": section.department,
                "semester": section.semester,
                "capacity": section.capacity,
                "enrolled_count": section.enrolled_count,
            },
            "slots": [
                {
                    "course_code": course.code,
                    "course_title": course.title,
                    "day_of_week": slot.day_of_week,
                    "start_time": slot.start_time.isoformat(timespec="minutes"),
                    "end_time": slot.end_time.isoformat(timespec="minutes"),
                    "instructor_id": (
                        str(slot.instructor_id) if slot.instructor_id else None
                    ),
                    "room": slot.room,
                }
                for slot, course in slot_rows
            ],
        }

    async def get_instructor_schedule(
        self,
        instructor_id: uuid.UUID,
        term_id: uuid.UUID,
    ) -> list[dict]:
        """
        Every ClassScheduleSlot taught by this instructor in the term,
        flattened across sections.
        """
        rows = (
            await self.db.execute(
                select(ClassScheduleSlot, Course, Section).join(
                    Course, Course.id == ClassScheduleSlot.course_id,
                ).join(
                    Section, Section.id == ClassScheduleSlot.section_id,
                ).where(
                    Section.term_id == term_id,
                    ClassScheduleSlot.instructor_id == instructor_id,
                ).order_by(
                    ClassScheduleSlot.day_of_week.asc(),
                    ClassScheduleSlot.start_time.asc(),
                )
            )
        ).all()
        return [
            {
                "section_id": str(sec.id),
                "section_code": sec.section_code,
                "department": sec.department,
                "semester": sec.semester,
                "room": slot.room,
                "course_code": course.code,
                "course_title": course.title,
                "day_of_week": slot.day_of_week,
                "start_time": slot.start_time.isoformat(timespec="minutes"),
                "end_time": slot.end_time.isoformat(timespec="minutes"),
            }
            for slot, course, sec in rows
        ]

    async def assign_instructor_to_slot(
        self,
        slot_id: uuid.UUID,
        instructor_id: uuid.UUID,
        officer_role: UserRole,
        officer_id: uuid.UUID,
    ) -> ClassScheduleSlot:
        """
        Officer-only: change the instructor pinned to one
        ``ClassScheduleSlot`` row. Refuses if the new instructor is
        already booked at the same ``(day_of_week, start_time)`` in
        any other slot in the term — that would create the same
        collision the generator avoids.

        Raises:
            UnauthorizedActorError: caller is not officer/admin.
            EntityNotFoundError: slot or instructor doesn't exist.
            InvalidAdjustmentRequestError: instructor already booked.
        """
        self._require_officer(officer_role)

        slot = (
            await self.db.execute(
                select(ClassScheduleSlot).where(
                    ClassScheduleSlot.id == slot_id,
                )
            )
        ).scalar_one_or_none()
        if slot is None:
            raise EntityNotFoundError("ClassScheduleSlot", str(slot_id))

        instructor = (
            await self.db.execute(
                select(Instructor).where(
                    Instructor.id == instructor_id,
                    Instructor.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()
        if instructor is None:
            raise EntityNotFoundError("Instructor", str(instructor_id))

        # Resolve the term via the slot's section so collision
        # checks are scoped to the term that owns this slot.
        slot_section = (
            await self.db.execute(
                select(Section).where(Section.id == slot.section_id)
            )
        ).scalar_one()

        collision = (
            await self.db.execute(
                select(ClassScheduleSlot.id)
                .join(Section, Section.id == ClassScheduleSlot.section_id)
                .where(
                    Section.term_id == slot_section.term_id,
                    ClassScheduleSlot.id != slot.id,
                    ClassScheduleSlot.instructor_id == instructor_id,
                    ClassScheduleSlot.day_of_week == slot.day_of_week,
                    ClassScheduleSlot.start_time == slot.start_time,
                )
            )
        ).scalar_one_or_none()
        if collision is not None:
            raise InvalidAdjustmentRequestError(
                f"Instructor {instructor.instructor_id} is already booked "
                f"on {slot.day_of_week} at "
                f"{slot.start_time.isoformat(timespec='minutes')} "
                f"in this term."
            )

        slot.instructor_id = instructor_id
        await self.db.commit()
        await self.db.refresh(slot)

        write_audit_log(
            action="course.slot.instructor_reassigned",
            actor_role=officer_role.value,
            actor_id=officer_id,
            resource_type="ClassScheduleSlot",
            resource_id=slot.id,
            decision="ok",
            metadata={
                "instructor_id": str(instructor_id),
                "section_id": str(slot.section_id),
                "course_id": str(slot.course_id),
                "day_of_week": slot.day_of_week,
                "start_time": slot.start_time.isoformat(timespec="minutes"),
            },
        )
        return slot

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
        """
        Materialise an ADD: insert (or un-drop) the
        ``RegistrationCourse`` row. The student's cohort
        ``Registration.section_id`` is unchanged — the cohort's
        weekly schedule is rebuilt the next time the officer runs
        scheduling.
        """
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
                is_dropped=False,
            ))
        else:
            existing_link.is_dropped = False

    async def _apply_drop(
        self, request: AddDropRequest, registration: Registration,
    ) -> None:
        """
        Materialise a DROP: flip ``is_dropped=True`` on the existing
        ``RegistrationCourse`` row. Section/cohort assignment is
        unaffected — only the per-course slots regenerate next
        scheduling run.
        """
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
        # Production wiring: if no agent is injected, build one with
        # the default LLM client (which itself returns None when
        # GEMINI_API_KEY is unset, so the agent stays rule-only).
        if advisory_agent is None:
            from app.ai.llm_client import build_default_llm_client
            advisory_agent = AcademicAdvisoryAgent(
                llm_client=build_default_llm_client(),
            )
        self.agent = advisory_agent

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
    course-management Student row, then issues portal credentials:

      1. Replaces the User's admission-time password with a hashed
         single-use 4-digit PIN.
      2. Sets ``User.must_change_password = True`` so the auth lockout
         middleware blocks every endpoint except /auth/change-password.
      3. Emails the student their UGR ID + the plaintext PIN.

    The PIN is the only time the plaintext appears anywhere — it is
    not stored, not logged, and not returned in the HTTP response.
    The officer who triggers onboarding never sees it either.

    Phase-1 contract: officer-triggered (REGISTRAR_OFFICER or ADMIN)
    via POST /api/v1/courses/officer/students/onboard-from-enrollment.
    Phase-2 will replace this with an event-bus subscription on an
    EnrollmentCompletedEvent published by the admission Enrollment Agent.

    Idempotent: re-running for an Enrollment whose Student already
    exists raises StudentAlreadyOnboardedError carrying the existing
    student_id, which the router maps to 409.
    """

    def __init__(
        self,
        db: AsyncSession,
        *,
        email_service: Optional["EmailService"] = None,
    ) -> None:
        self.db = db
        self.students = StudentRepository(db)
        self._email_service = email_service

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

        # Pull the User row — we need it both for display name and to
        # overwrite the password with the temporary PIN.
        from app.modules.auth.models import User
        user = await self.db.get(User, enrollment.applicant_id)
        if user is None:
            raise EntityNotFoundError("User", str(enrollment.applicant_id))
        full_name = f"{user.first_name} {user.last_name}".strip() or user.email

        # Denormalise sponsorship_type from the admission record so
        # the student doesn't have to re-state it on every
        # registration. Falls back to None if the application can't
        # be resolved (e.g. test fixtures that build Enrollment rows
        # without a real Application).
        from app.modules.undergraduate.models import UndergraduateApplication
        application = await self.db.get(
            UndergraduateApplication, enrollment.application_id,
        )
        sponsorship = (
            application.sponsorship_type if application is not None else None
        )

        # Issue the portal credentials. The plaintext PIN exists only
        # in this scope — we hash it for storage, hand it to the email
        # template, then let it fall out of scope.
        from app.core.security import generate_temporary_pin, hash_password
        temporary_pin = generate_temporary_pin(digits=4)
        user.hashed_password = hash_password(temporary_pin)
        user.must_change_password = True

        student = Student(
            user_id=enrollment.applicant_id,
            student_id=enrollment.university_id,
            full_name=full_name,
            current_semester=1,                    # fresh admit
            department=enrollment.department,      # denormalised for curriculum filter
            sponsorship_type=sponsorship,          # denormalised from admission
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
                # PIN is intentionally omitted from the audit payload.
                "portal_credentials_issued": True,
            },
        )
        await self.db.commit()
        await self.db.refresh(student)

        # Email delivery is best-effort: an SMTP outage cannot reverse
        # an enrollment. The officer can re-issue credentials by hand
        # if the email never arrives.
        if self._email_service is not None:
            from app.shared.email import build_portal_credentials_email
            try:
                await self._email_service.send(
                    build_portal_credentials_email(
                        to_email=user.email,
                        first_name=user.first_name,
                        student_id=student.student_id,
                        temporary_pin=temporary_pin,
                    )
                )
            except Exception:
                logger.exception(
                    "Portal-credentials email delivery failed for %s",
                    user.email,
                )

        return student


# ════════════════════════════════════════════════════════════════
#  InstructorService — Department-Head-driven instructor management
# ════════════════════════════════════════════════════════════════


class InstructorService:
    """
    Department-Head-driven instructor lifecycle. Three main flows:

      1. add_instructor: create the User + Instructor rows, generate
         a 4-digit PIN, set must_change_password=True, and email the
         credentials. Mirrors OnboardingService's PIN flow exactly so
         instructors and students share one auth contract.
      2. assign_to_course: upsert the (instructor, course, term)
         InstructorAssignment row. Reposting with a new instructor
         silently rebinds — every (course, term) pair has at most one
         active instructor.
      3. unassign: delete an InstructorAssignment row.

    All write methods are gated to ``OfficerRole.DEPARTMENT_HEAD`` (or
    user-level ``UserRole.ADMIN`` as an escape hatch); read methods
    accept any logged-in user.
    """

    def __init__(
        self,
        db: AsyncSession,
        *,
        email_service: Optional["EmailService"] = None,
    ) -> None:
        self.db = db
        self._email_service = email_service

    # ── Authorisation helper ─────────────────────────────────────

    async def _require_dh_or_admin(
        self,
        officer_user_id: uuid.UUID,
    ) -> tuple[Optional[CourseManagementOfficer], UserRole]:
        """
        Resolve the calling user; raise UnauthorizedActorError unless
        they are an ADMIN user OR have a DEPARTMENT_HEAD officer row.
        Returns (officer_row_or_None, user_role).
        """
        from app.modules.auth.models import User
        user = await self.db.get(User, officer_user_id)
        if user is None:
            raise UnauthorizedActorError(
                "Calling user not found."
            )
        if user.role == UserRole.ADMIN:
            return None, UserRole.ADMIN
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
                "Only a Department Head (or ADMIN) may manage instructors "
                "and instructor assignments."
            )
        return officer, user.role

    # ── add_instructor ──────────────────────────────────────────

    async def add_instructor(
        self,
        *,
        staff_id: str,
        email: str,
        first_name: str,
        last_name: str,
        department: str,
        officer_user_id: uuid.UUID,
    ) -> tuple[Instructor, str]:
        """
        Create a new instructor profile + portal credentials.

        Returns ``(instructor_row, plaintext_pin)`` so callers can
        log/email the PIN; the row never persists the plaintext.
        """
        await self._require_dh_or_admin(officer_user_id)

        if not staff_id.strip() or not email.strip():
            raise InvalidAdjustmentRequestError(
                "staff_id and email are required."
            )

        # Idempotency: if an Instructor with this staff_id already
        # exists, surface it as a 409.
        existing = (
            await self.db.execute(
                select(Instructor).where(
                    Instructor.instructor_id == staff_id.strip(),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise InvalidAdjustmentRequestError(
                f"Instructor {staff_id} already exists."
            )

        from app.core.security import generate_temporary_pin, hash_password
        from app.modules.auth.models import User

        # Email uniqueness — a User row may already exist if this
        # person previously held a different role.
        existing_user = (
            await self.db.execute(
                select(User).where(User.email == email.strip())
            )
        ).scalar_one_or_none()
        if existing_user is not None:
            raise InvalidAdjustmentRequestError(
                f"A user with email {email} already exists."
            )

        pin = generate_temporary_pin(digits=4)
        user = User(
            email=email.strip(),
            first_name=first_name.strip(),
            last_name=last_name.strip(),
            hashed_password=hash_password(pin),
            role=UserRole.INSTRUCTOR,
            is_active=True,
            must_change_password=True,
        )
        self.db.add(user)
        await self.db.flush()

        instructor = Instructor(
            user_id=user.id,
            instructor_id=staff_id.strip(),
            department=department.strip(),
        )
        self.db.add(instructor)
        await self.db.flush()

        write_audit_log(
            action="course.instructor.created",
            actor_role=UserRole.AGENT.value,    # service action
            actor_id=officer_user_id,
            resource_type="Instructor",
            resource_id=instructor.id,
            decision="created",
            metadata={
                "staff_id": staff_id.strip(),
                "department": department.strip(),
                "portal_credentials_issued": True,
                # PIN deliberately omitted from audit metadata.
            },
        )
        await self.db.commit()
        await self.db.refresh(instructor)

        # Best-effort email — same pattern as OnboardingService.
        if self._email_service is not None:
            from app.shared.email import build_portal_credentials_email
            try:
                await self._email_service.send(
                    build_portal_credentials_email(
                        to_email=user.email,
                        first_name=user.first_name,
                        student_id=instructor.instructor_id,
                        temporary_pin=pin,
                    )
                )
            except Exception:
                logger.exception(
                    "Portal-credentials email delivery failed for instructor %s",
                    user.email,
                )

        return instructor, pin

    # ── list_instructors (read; any logged-in user) ─────────────

    async def list_instructors(
        self,
        *,
        department: Optional[str] = None,
    ) -> list[Instructor]:
        stmt = select(Instructor).where(
            Instructor.is_deleted == False,  # noqa: E712
        )
        if department:
            stmt = stmt.where(Instructor.department == department)
        stmt = stmt.order_by(Instructor.instructor_id.asc())
        return list((await self.db.execute(stmt)).scalars().all())

    # ── assign_to_course (DH or ADMIN; upsert) ──────────────────

    async def assign_to_course(
        self,
        *,
        instructor_id: uuid.UUID,
        course_id: uuid.UUID,
        term_id: uuid.UUID,
        officer_user_id: uuid.UUID,
    ) -> InstructorAssignment:
        """
        Bind an instructor to a (course, term). At most one active
        assignment per (course, term) — re-posting with a different
        instructor silently rebinds.
        """
        await self._require_dh_or_admin(officer_user_id)

        # Resolve all three refs so 404s are clean
        instructor = await self.db.get(Instructor, instructor_id)
        if instructor is None or instructor.is_deleted:
            raise EntityNotFoundError("Instructor", str(instructor_id))
        course = await self.db.get(Course, course_id)
        if course is None or course.is_deleted:
            raise EntityNotFoundError("Course", str(course_id))
        term = await self.db.get(AcademicTerm, term_id)
        if term is None:
            raise EntityNotFoundError("AcademicTerm", str(term_id))

        existing = (
            await self.db.execute(
                select(InstructorAssignment).where(
                    InstructorAssignment.course_id == course_id,
                    InstructorAssignment.term_id == term_id,
                ).order_by(InstructorAssignment.created_at.desc())
            )
        ).scalars().first()

        action = "course.instructor_assignment.created"
        if existing is None:
            assignment = InstructorAssignment(
                instructor_id=instructor_id,
                course_id=course_id,
                term_id=term_id,
            )
            self.db.add(assignment)
            await self.db.flush()
        else:
            previous_instructor_id = existing.instructor_id
            existing.instructor_id = instructor_id
            assignment = existing
            await self.db.flush()
            action = "course.instructor_assignment.updated"

        write_audit_log(
            action=action,
            actor_role=UserRole.AGENT.value,
            actor_id=officer_user_id,
            resource_type="InstructorAssignment",
            resource_id=assignment.id,
            decision="ok",
            metadata={
                "instructor_id": str(instructor_id),
                "course_id": str(course_id),
                "term_id": str(term_id),
            },
        )
        await self.db.commit()
        await self.db.refresh(assignment)
        return assignment

    # ── list_assignments (read; any logged-in user) ─────────────

    async def list_assignments(
        self,
        *,
        term_id: Optional[uuid.UUID] = None,
        course_id: Optional[uuid.UUID] = None,
        instructor_id: Optional[uuid.UUID] = None,
    ) -> list[InstructorAssignment]:
        stmt = select(InstructorAssignment)
        if term_id:
            stmt = stmt.where(InstructorAssignment.term_id == term_id)
        if course_id:
            stmt = stmt.where(InstructorAssignment.course_id == course_id)
        if instructor_id:
            stmt = stmt.where(
                InstructorAssignment.instructor_id == instructor_id,
            )
        stmt = stmt.order_by(InstructorAssignment.created_at.asc())
        return list((await self.db.execute(stmt)).scalars().all())

    # ── unassign ───────────────────────────────────────────────

    async def unassign(
        self,
        *,
        assignment_id: uuid.UUID,
        officer_user_id: uuid.UUID,
    ) -> None:
        await self._require_dh_or_admin(officer_user_id)
        assignment = await self.db.get(InstructorAssignment, assignment_id)
        if assignment is None:
            raise EntityNotFoundError(
                "InstructorAssignment", str(assignment_id),
            )
        write_audit_log(
            action="course.instructor_assignment.removed",
            actor_role=UserRole.AGENT.value,
            actor_id=officer_user_id,
            resource_type="InstructorAssignment",
            resource_id=assignment.id,
            decision="removed",
            metadata={
                "instructor_id": str(assignment.instructor_id),
                "course_id": str(assignment.course_id),
                "term_id": str(assignment.term_id),
            },
        )
        await self.db.delete(assignment)
        await self.db.commit()
