"""
Track A — Department-Head prerequisite override (SRS §3.5).

Confirms:
  - Only an officer with role=DEPARTMENT_HEAD can grant the override
  - The CurriculumComplianceAgent skips the prereq check for any
    course that has an active PrerequisiteOverride
  - Idempotency on (registration, course) pair
  - End-to-end: a registration that previously bounced with a prereq
    miss now passes after a Department Head grants the override
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.modules.auth.models import User
from app.modules.course.agents import CurriculumComplianceAgent
from app.modules.course.exceptions import (
    ComplianceCheckFailedError, EntityNotFoundError,
    InvalidAdjustmentRequestError, UnauthorizedActorError,
)
from app.modules.course.models import (
    Course, CourseManagementOfficer, CoursePrerequisite,
    PrerequisiteOverride, Registration, RegistrationCourse,
)
from app.modules.course.service import RegistrationService
from app.modules.course.services import PayMock
from app.shared.enums import (
    OfficerRole, RegistrationStatus, SponsorshipType, UserRole,
)


# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture
def isolated_pay_mock() -> PayMock:
    return PayMock()


@pytest.fixture
def reg_service(async_session, isolated_pay_mock):
    agent = CurriculumComplianceAgent(
        agent_id="AGENT_CCA_OVR",
        payment_service=isolated_pay_mock,
    )
    return RegistrationService(async_session, compliance_agent=agent)


@pytest_asyncio.fixture
async def cs_chain(async_session) -> dict[str, Course]:
    """Credits sized so 2-course registrations (12 ECTS) clear the
    credit-load floor."""
    cs101 = Course(
        code="CS101", title="Intro", credit_hours=12, semester=1,
        department="Computer Science",
    )
    cs201 = Course(
        code="CS201", title="Data Structures", credit_hours=12, semester=2,
        department="Computer Science",
    )
    async_session.add_all([cs101, cs201])
    await async_session.flush()

    async_session.add(
        CoursePrerequisite(course_id=cs201.id, prerequisite_course_id=cs101.id)
    )
    await async_session.flush()
    return {"CS101": cs101, "CS201": cs201}


@pytest_asyncio.fixture
async def department_head(async_session) -> CourseManagementOfficer:
    user = User(
        id=uuid.uuid4(),
        email="dept-head-test@aau.edu.et",
        first_name="Dept", last_name="Head",
        hashed_password="x", role=UserRole.REGISTRAR_OFFICER, is_active=True,
    )
    async_session.add(user)
    await async_session.flush()
    officer = CourseManagementOfficer(
        user_id=user.id,
        staff_id="REG/9100/10",
        role=OfficerRole.DEPARTMENT_HEAD,
        authorization_level=3,
    )
    async_session.add(officer)
    await async_session.flush()
    return officer


@pytest_asyncio.fixture
async def plain_registrar_officer(async_session) -> CourseManagementOfficer:
    user = User(
        id=uuid.uuid4(),
        email="reg-officer-test@aau.edu.et",
        first_name="Reg", last_name="Officer",
        hashed_password="x", role=UserRole.REGISTRAR_OFFICER, is_active=True,
    )
    async_session.add(user)
    await async_session.flush()
    officer = CourseManagementOfficer(
        user_id=user.id,
        staff_id="REG/9101/10",
        role=OfficerRole.REGISTRAR_OFFICER,
        authorization_level=5,
    )
    async_session.add(officer)
    await async_session.flush()
    return officer


@pytest_asyncio.fixture
async def open_term(async_session, seeded_term):
    seeded_term.is_open = True
    await async_session.flush()
    return seeded_term


@pytest_asyncio.fixture
async def cs201_registration(
    async_session, reg_service, open_term, seeded_student, cs_chain,
) -> Registration:
    """Draft registration containing CS201 (which requires CS101)."""
    reg = await reg_service.create_draft(
        seeded_student.id, open_term.id, SponsorshipType.SELF_SPONSORED,
    )
    await reg_service.add_course_to_draft(reg.id, cs_chain["CS201"].id)
    return reg


# ── Authorisation ──────────────────────────────────────────────


async def test_department_head_can_grant_override(
    async_session, reg_service, cs201_registration, cs_chain, department_head,
):
    override = await reg_service.grant_prerequisite_override(
        registration_id=cs201_registration.id,
        course_id=cs_chain["CS201"].id,
        officer_user_id=department_head.user_id,
        justification="Student has equivalent industry experience.",
    )
    assert override.granted_by_id == department_head.user_id
    assert override.course_id == cs_chain["CS201"].id


async def test_plain_registrar_officer_cannot_grant_override(
    reg_service, cs201_registration, cs_chain, plain_registrar_officer,
):
    """SRS §3.5: only DEPARTMENT_HEAD, not regular registrar."""
    with pytest.raises(UnauthorizedActorError):
        await reg_service.grant_prerequisite_override(
            registration_id=cs201_registration.id,
            course_id=cs_chain["CS201"].id,
            officer_user_id=plain_registrar_officer.user_id,
            justification="Trying to override as registrar.",
        )


async def test_unknown_officer_cannot_grant_override(
    reg_service, cs201_registration, cs_chain,
):
    """A user with no CourseManagementOfficer row at all is rejected."""
    with pytest.raises(UnauthorizedActorError):
        await reg_service.grant_prerequisite_override(
            registration_id=cs201_registration.id,
            course_id=cs_chain["CS201"].id,
            officer_user_id=uuid.uuid4(),
            justification="No officer record.",
        )


# ── Validation ─────────────────────────────────────────────────


async def test_empty_justification_rejected(
    reg_service, cs201_registration, cs_chain, department_head,
):
    with pytest.raises(InvalidAdjustmentRequestError):
        await reg_service.grant_prerequisite_override(
            registration_id=cs201_registration.id,
            course_id=cs_chain["CS201"].id,
            officer_user_id=department_head.user_id,
            justification="   ",
        )


async def test_unknown_registration_404(
    reg_service, cs_chain, department_head,
):
    with pytest.raises(EntityNotFoundError):
        await reg_service.grant_prerequisite_override(
            registration_id=uuid.uuid4(),
            course_id=cs_chain["CS201"].id,
            officer_user_id=department_head.user_id,
            justification="Has documented exception.",
        )


async def test_duplicate_override_rejected(
    reg_service, cs201_registration, cs_chain, department_head,
):
    await reg_service.grant_prerequisite_override(
        registration_id=cs201_registration.id,
        course_id=cs_chain["CS201"].id,
        officer_user_id=department_head.user_id,
        justification="Original override.",
    )
    with pytest.raises(InvalidAdjustmentRequestError) as exc_info:
        await reg_service.grant_prerequisite_override(
            registration_id=cs201_registration.id,
            course_id=cs_chain["CS201"].id,
            officer_user_id=department_head.user_id,
            justification="Duplicate attempt.",
        )
    assert "already exists" in exc_info.value.detail.lower()


# ── End-to-end: override unblocks a previously-blocked submit ──


async def test_submit_blocked_by_prereq_clears_after_override(
    async_session,
    reg_service,
    isolated_pay_mock,
    cs201_registration,
    cs_chain,
    seeded_student,
    department_head,
):
    """
    Sara submits CS201 without having passed CS101 → bounce.
    Dept Head grants override → Sara resubmits → REGISTERED.
    """
    isolated_pay_mock.set_payment_status(
        seeded_student.id, cs_chain["CS201"].id, paid=True,
    )

    # First attempt: prereq miss bounces back
    with pytest.raises(ComplianceCheckFailedError):
        await reg_service.submit(
            cs201_registration.id, student_user_id=seeded_student.user_id,
        )
    bounced = await reg_service.registrations.get(cs201_registration.id)
    assert bounced.status == RegistrationStatus.REGISTRATION_OPEN

    # Department Head grants override
    await reg_service.grant_prerequisite_override(
        registration_id=cs201_registration.id,
        course_id=cs_chain["CS201"].id,
        officer_user_id=department_head.user_id,
        justification="Equivalent prior coursework verified by department.",
    )

    # Resubmit: now clears
    finalised, compliance = await reg_service.submit(
        cs201_registration.id, student_user_id=seeded_student.user_id,
    )
    assert finalised.status == RegistrationStatus.REGISTERED
    assert compliance["overall_passed"] is True

    # Verify the per-course prereq result records the override path
    cs201_result = next(
        r for r in compliance["prereq_results"]
        if r["course_id"] == str(cs_chain["CS201"].id)
    )
    assert cs201_result["passed"] is True
    assert cs201_result["details"].get("via_prerequisite_override") is True


async def test_override_does_not_affect_unrelated_registrations(
    async_session, reg_service, isolated_pay_mock, open_term, cs_chain,
    department_head,
):
    """
    Override on registration A's CS201 must not leak into a different
    student's registration B.
    """
    # Build a second student
    other_user = User(
        id=uuid.uuid4(),
        email="other-student-prereq@aau.edu.et",
        first_name="Other", last_name="Student",
        hashed_password="x", role=UserRole.STUDENT, is_active=True,
    )
    async_session.add(other_user)
    await async_session.flush()
    from app.modules.course.models import Student
    from app.shared.enums import EnrollmentStatus
    other_student = Student(
        user_id=other_user.id,
        student_id="UGR/0099/14",
        full_name="Other Student",
        current_semester=2,
        enrollment_status=EnrollmentStatus.ACTIVE,
    )
    async_session.add(other_student)
    await async_session.flush()

    # Registration A: gets the override
    reg_a = await reg_service.create_draft(
        other_student.id, open_term.id, SponsorshipType.SELF_SPONSORED,
    )
    await reg_service.add_course_to_draft(reg_a.id, cs_chain["CS201"].id)
    isolated_pay_mock.set_payment_status(
        other_student.id, cs_chain["CS201"].id, paid=True,
    )
    await reg_service.grant_prerequisite_override(
        registration_id=reg_a.id,
        course_id=cs_chain["CS201"].id,
        officer_user_id=department_head.user_id,
        justification="Documented exception for student A.",
    )
    finalised_a, _ = await reg_service.submit(
        reg_a.id, student_user_id=other_user.id,
    )
    assert finalised_a.status == RegistrationStatus.REGISTERED

    # No PrerequisiteOverride exists for any other registration
    other_overrides = (
        await async_session.execute(
            select(PrerequisiteOverride).where(
                PrerequisiteOverride.registration_id != reg_a.id,
            )
        )
    ).scalars().all()
    assert other_overrides == []
