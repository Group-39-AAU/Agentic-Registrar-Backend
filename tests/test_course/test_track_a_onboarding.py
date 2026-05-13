"""
Track A — Enrollment → Student bridge tests.

Confirms the OnboardingService correctly materialises a course-
management Student row from an admission-module Enrollment record.
This is the seam between the undergraduate admission lifecycle and
the course-management lifecycle that lets an admitted Sara actually
hit the registration portal.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.modules.auth.models import User
from app.modules.course.exceptions import (
    EntityNotFoundError, StudentAlreadyOnboardedError, UnauthorizedActorError,
)
from app.modules.course.models import Student
from app.modules.course.service import OnboardingService
from app.modules.undergraduate.enrollment.models import Enrollment
from app.modules.undergraduate.models import (
    UndergraduateAdmissionTerm, UndergraduateApplication,
)
from app.shared.enums import (
    ApplicationStatus, EnrollmentStatus, SponsorshipType, StreamType, UserRole,
)
from datetime import date


# ── Fixtures ────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def admitted_user(async_session) -> User:
    """A User row representing an applicant who has been admitted."""
    user = User(
        id=uuid.uuid4(),
        email="sara.admitted@aau.edu.et",
        first_name="Sara",
        last_name="Mekonnen",
        hashed_password="x",
        role=UserRole.STUDENT,
        is_active=True,
    )
    async_session.add(user)
    await async_session.flush()
    return user


@pytest_asyncio.fixture
async def admission_term(async_session) -> UndergraduateAdmissionTerm:
    term = UndergraduateAdmissionTerm(
        term_name="Admission Fall 2026",
        start_date=date(2026, 7, 1),
        end_date=date(2026, 8, 31),
        is_open=False,
    )
    async_session.add(term)
    await async_session.flush()
    return term


@pytest_asyncio.fixture
async def admission_application(
    async_session, admitted_user, admission_term,
) -> UndergraduateApplication:
    app = UndergraduateApplication(
        applicant_id=admitted_user.id,
        sponsorship_type=SponsorshipType.GOVERNMENT,
        stream=StreamType.NATURAL,
        admission_number="2955397",
        admission_term_id=admission_term.id,
        current_status=ApplicationStatus.ENROLLED,
    )
    async_session.add(app)
    await async_session.flush()
    return app


@pytest_asyncio.fixture
async def admission_enrollment(
    async_session, admitted_user, admission_application,
) -> Enrollment:
    enrollment = Enrollment(
        application_id=admission_application.id,
        applicant_id=admitted_user.id,
        admission_term_id=admission_application.admission_term_id,
        university_id="UGR/9001/14",
        department="Computer Science",
        enrollment_term="Fall 2026",
    )
    async_session.add(enrollment)
    await async_session.flush()
    return enrollment


# ── Happy path ──────────────────────────────────────────────────


async def test_onboard_creates_student_from_enrollment(
    async_session, admission_enrollment, admitted_user,
):
    svc = OnboardingService(async_session)
    student = await svc.onboard_student_from_enrollment(
        enrollment_id=admission_enrollment.id,
        officer_role=UserRole.REGISTRAR_OFFICER,
        officer_id=uuid.uuid4(),
    )
    assert student.user_id == admitted_user.id
    assert student.student_id == "UGR/9001/14"
    assert student.full_name == "Sara Mekonnen"
    assert student.current_semester == 1
    assert student.enrollment_status == EnrollmentStatus.ACTIVE


async def test_onboard_persists_student_to_db(
    async_session, admission_enrollment,
):
    svc = OnboardingService(async_session)
    created = await svc.onboard_student_from_enrollment(
        enrollment_id=admission_enrollment.id,
        officer_role=UserRole.REGISTRAR_OFFICER,
        officer_id=uuid.uuid4(),
    )
    fetched = (
        await async_session.execute(
            select(Student).where(Student.id == created.id)
        )
    ).scalar_one()
    assert fetched.student_id == "UGR/9001/14"


# ── Authorisation ──────────────────────────────────────────────


async def test_onboard_rejects_student_role(
    async_session, admission_enrollment,
):
    svc = OnboardingService(async_session)
    with pytest.raises(UnauthorizedActorError):
        await svc.onboard_student_from_enrollment(
            enrollment_id=admission_enrollment.id,
            officer_role=UserRole.STUDENT,
            officer_id=uuid.uuid4(),
        )


async def test_onboard_accepts_admin_role(
    async_session, admission_enrollment,
):
    svc = OnboardingService(async_session)
    student = await svc.onboard_student_from_enrollment(
        enrollment_id=admission_enrollment.id,
        officer_role=UserRole.ADMIN,
        officer_id=uuid.uuid4(),
    )
    assert student is not None


# ── Idempotency / errors ───────────────────────────────────────


async def test_onboard_rejects_double_onboarding(
    async_session, admission_enrollment,
):
    svc = OnboardingService(async_session)
    await svc.onboard_student_from_enrollment(
        enrollment_id=admission_enrollment.id,
        officer_role=UserRole.REGISTRAR_OFFICER,
        officer_id=uuid.uuid4(),
    )
    with pytest.raises(StudentAlreadyOnboardedError) as exc_info:
        await svc.onboard_student_from_enrollment(
            enrollment_id=admission_enrollment.id,
            officer_role=UserRole.REGISTRAR_OFFICER,
            officer_id=uuid.uuid4(),
        )
    assert exc_info.value.student_id == "UGR/9001/14"


async def test_onboard_404_on_unknown_enrollment(async_session):
    svc = OnboardingService(async_session)
    with pytest.raises(EntityNotFoundError) as exc_info:
        await svc.onboard_student_from_enrollment(
            enrollment_id=uuid.uuid4(),
            officer_role=UserRole.REGISTRAR_OFFICER,
            officer_id=uuid.uuid4(),
        )
    assert exc_info.value.entity == "Enrollment"


async def test_onboard_handles_missing_user_name_gracefully(
    async_session, admission_term,
):
    """If first_name/last_name are blank, fall back to email."""
    user = User(
        id=uuid.uuid4(), email="noname@aau.edu.et",
        first_name="", last_name="",
        hashed_password="x", role=UserRole.STUDENT, is_active=True,
    )
    async_session.add(user)
    await async_session.flush()

    app = UndergraduateApplication(
        applicant_id=user.id,
        sponsorship_type=SponsorshipType.GOVERNMENT,
        stream=StreamType.NATURAL,
        admission_number="9999999",
        admission_term_id=admission_term.id,
        current_status=ApplicationStatus.ENROLLED,
    )
    async_session.add(app)
    await async_session.flush()

    enrollment = Enrollment(
        application_id=app.id,
        applicant_id=user.id,
        admission_term_id=app.admission_term_id,
        university_id="UGR/9999/14",
        department="Computer Science",
        enrollment_term="Fall 2026",
    )
    async_session.add(enrollment)
    await async_session.flush()

    svc = OnboardingService(async_session)
    student = await svc.onboard_student_from_enrollment(
        enrollment_id=enrollment.id,
        officer_role=UserRole.REGISTRAR_OFFICER,
        officer_id=uuid.uuid4(),
    )
    assert student.full_name == "noname@aau.edu.et"
