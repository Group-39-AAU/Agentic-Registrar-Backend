"""
Track A — government cost-sharing form tests.

Drives RegistrationService.submit_cost_sharing_form through every
branch:

  - Government-sponsored happy path: every active course in the draft
    is marked paid in the module-level pay_mock singleton.
  - Self-sponsored registrations are rejected with 409 (they must use
    /payment/initiate + /payment/callback instead).
  - Submitting on a PAYMENT_HOLD registration clears the hold back to
    REGISTRATION_OPEN so the student can resubmit.
  - Ownership: another user calling on someone else's registration is
    rejected.
  - Empty registration is rejected (no courses to cover).

The cost-sharing form mutates the *module-level* pay_mock singleton
(matching how the bursar callback works), so tests reset it on the
way in and the way out.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from app.modules.auth.models import User
from app.modules.course.exceptions import (
    InvalidAdjustmentRequestError,
    InvalidStateTransitionError,
    UnauthorizedActorError,
)
from app.modules.course.models import (
    Course, Registration, RegistrationCourse, RegistrationStatusHistory,
)
from app.modules.course.service import RegistrationService
from app.modules.course.services import pay_mock
from app.shared.enums import (
    EnrollmentStatus, RegistrationStatus, SponsorshipType, UserRole,
)


# ── Pay-mock isolation ──────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_pay_mock_singleton():
    """Wipe the module singleton before and after every test."""
    pay_mock.reset()
    yield
    pay_mock.reset()


# ── Fixtures ────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def gov_student(async_session, seeded_student) -> User:
    """Promote the seeded student into a government-sponsored cohort."""
    seeded_student.sponsorship_type = SponsorshipType.GOVERNMENT
    await async_session.flush()
    return seeded_student


@pytest_asyncio.fixture
async def cs_courses(async_session) -> list[Course]:
    """Two CS courses the student will register for."""
    courses = [
        Course(
            code="CS101", title="Intro", credit_hours=4, semester=1,
            department="Computer Science",
        ),
        Course(
            code="MATH101", title="Calculus I", credit_hours=4, semester=1,
            department="Mathematics",
        ),
    ]
    async_session.add_all(courses)
    await async_session.flush()
    return courses


async def _build_registration(
    session,
    student,
    term,
    courses,
    *,
    sponsorship: SponsorshipType,
    status: RegistrationStatus = RegistrationStatus.REGISTRATION_OPEN,
) -> Registration:
    reg = Registration(
        student_id=student.id,
        term_id=term.id,
        status=status,
        sponsorship_type=sponsorship,
    )
    session.add(reg)
    await session.flush()
    for c in courses:
        session.add(RegistrationCourse(
            registration_id=reg.id,
            course_id=c.id,
            is_dropped=False,
        ))
    await session.flush()
    await session.refresh(reg, attribute_names=["courses"])
    return reg


@pytest.fixture
def reg_service(async_session) -> RegistrationService:
    return RegistrationService(async_session)


# ── Happy path ──────────────────────────────────────────────────


async def test_gov_form_marks_every_course_paid(
    async_session, reg_service, gov_student, seeded_term, cs_courses,
):
    reg = await _build_registration(
        async_session, gov_student, seeded_term, cs_courses,
        sponsorship=SponsorshipType.GOVERNMENT,
    )

    payload = await reg_service.submit_cost_sharing_form(
        registration_id=reg.id, student_user_id=gov_student.user_id,
    )

    assert payload["course_count"] == 2
    assert set(payload["marked_paid_course_ids"]) == {
        str(c.id) for c in cs_courses
    }
    # Every (student, course) pair is now flagged paid in the singleton
    for c in cs_courses:
        assert pay_mock.get_payment_status(gov_student.id, c.id) is True


async def test_gov_form_response_carries_sponsorship_label(
    async_session, reg_service, gov_student, seeded_term, cs_courses,
):
    reg = await _build_registration(
        async_session, gov_student, seeded_term, cs_courses,
        sponsorship=SponsorshipType.GOVERNMENT,
    )

    payload = await reg_service.submit_cost_sharing_form(
        registration_id=reg.id, student_user_id=gov_student.user_id,
    )

    assert payload["sponsorship_type"] == SponsorshipType.GOVERNMENT.value


async def test_gov_form_skips_dropped_courses(
    async_session, reg_service, gov_student, seeded_term, cs_courses,
):
    """A course flagged is_dropped must not be marked paid by the form."""
    reg = await _build_registration(
        async_session, gov_student, seeded_term, cs_courses,
        sponsorship=SponsorshipType.GOVERNMENT,
    )
    # Flip CS101 to dropped before submitting the form
    cs101 = cs_courses[0]
    for rc in reg.courses:
        if rc.course_id == cs101.id:
            rc.is_dropped = True
    await async_session.flush()

    payload = await reg_service.submit_cost_sharing_form(
        registration_id=reg.id, student_user_id=gov_student.user_id,
    )

    assert payload["course_count"] == 1
    # MATH101 is paid; CS101 is not
    assert pay_mock.get_payment_status(gov_student.id, cs_courses[1].id) is True
    assert pay_mock.get_payment_status(gov_student.id, cs_courses[0].id) is False


async def test_gov_form_is_idempotent(
    async_session, reg_service, gov_student, seeded_term, cs_courses,
):
    """Re-running the form on the same registration is a no-op."""
    reg = await _build_registration(
        async_session, gov_student, seeded_term, cs_courses,
        sponsorship=SponsorshipType.GOVERNMENT,
    )

    first = await reg_service.submit_cost_sharing_form(
        registration_id=reg.id, student_user_id=gov_student.user_id,
    )
    second = await reg_service.submit_cost_sharing_form(
        registration_id=reg.id, student_user_id=gov_student.user_id,
    )

    assert first == second


# ── Sponsorship gate ────────────────────────────────────────────


async def test_self_sponsored_rejected_with_409(
    async_session, reg_service, seeded_student, seeded_term, cs_courses,
):
    """Self-sponsored students must use the regular payment endpoints."""
    seeded_student.sponsorship_type = SponsorshipType.SELF_SPONSORED
    await async_session.flush()
    reg = await _build_registration(
        async_session, seeded_student, seeded_term, cs_courses,
        sponsorship=SponsorshipType.SELF_SPONSORED,
    )

    with pytest.raises(InvalidAdjustmentRequestError) as ei:
        await reg_service.submit_cost_sharing_form(
            registration_id=reg.id, student_user_id=seeded_student.user_id,
        )
    assert "government-sponsored" in ei.value.detail.lower()
    # No payments were marked
    for c in cs_courses:
        assert pay_mock.get_payment_status(seeded_student.id, c.id) is False


# ── Payment-hold path ───────────────────────────────────────────


async def test_form_clears_payment_hold_back_to_registration_open(
    async_session, reg_service, gov_student, seeded_term, cs_courses,
):
    """Submitting the form on a PAYMENT_HOLD reg moves it back to OPEN."""
    reg = await _build_registration(
        async_session, gov_student, seeded_term, cs_courses,
        sponsorship=SponsorshipType.GOVERNMENT,
        status=RegistrationStatus.PAYMENT_HOLD,
    )

    await reg_service.submit_cost_sharing_form(
        registration_id=reg.id, student_user_id=gov_student.user_id,
    )

    await async_session.refresh(reg)
    assert reg.status == RegistrationStatus.REGISTRATION_OPEN
    # Status history records the transition with a clear reason
    history_rows = (
        await async_session.execute(
            RegistrationStatusHistory.__table__.select().where(
                RegistrationStatusHistory.registration_id == reg.id,
            )
        )
    ).all()
    assert any(
        "Cost-sharing form" in (row[0].trigger_reason if hasattr(row[0], 'trigger_reason') else row.trigger_reason)
        for row in history_rows
    ) if False else True   # see comment below
    # The above query returns Row objects; rather than fight SQLAlchemy
    # row shapes, sample via an ORM query.
    from sqlalchemy import select
    history = (
        await async_session.execute(
            select(RegistrationStatusHistory).where(
                RegistrationStatusHistory.registration_id == reg.id,
            )
        )
    ).scalars().all()
    assert any(
        "cost-sharing" in (h.trigger_reason or "").lower() for h in history
    )


# ── Ownership ───────────────────────────────────────────────────


async def test_form_rejects_non_owner(
    async_session, reg_service, gov_student, seeded_term, cs_courses,
):
    """A different user calling on someone else's registration is blocked."""
    reg = await _build_registration(
        async_session, gov_student, seeded_term, cs_courses,
        sponsorship=SponsorshipType.GOVERNMENT,
    )
    intruder = User(
        id=uuid.uuid4(),
        email="intruder@aau.edu.et",
        first_name="Intr", last_name="Uder",
        hashed_password="x", role=UserRole.STUDENT, is_active=True,
    )
    async_session.add(intruder)
    await async_session.flush()

    with pytest.raises(UnauthorizedActorError):
        await reg_service.submit_cost_sharing_form(
            registration_id=reg.id, student_user_id=intruder.id,
        )


# ── Empty registration ─────────────────────────────────────────


async def test_form_rejects_registration_with_no_active_courses(
    async_session, reg_service, gov_student, seeded_term, cs_courses,
):
    reg = await _build_registration(
        async_session, gov_student, seeded_term, cs_courses,
        sponsorship=SponsorshipType.GOVERNMENT,
    )
    # Drop every course, leaving the reg empty of active enrollments
    for rc in reg.courses:
        rc.is_dropped = True
    await async_session.flush()

    with pytest.raises(InvalidAdjustmentRequestError) as ei:
        await reg_service.submit_cost_sharing_form(
            registration_id=reg.id, student_user_id=gov_student.user_id,
        )
    assert "no active courses" in ei.value.detail.lower()


# ── Past-state guards ──────────────────────────────────────────


async def test_form_rejected_after_registration_finalised(
    async_session, reg_service, gov_student, seeded_term, cs_courses,
):
    reg = await _build_registration(
        async_session, gov_student, seeded_term, cs_courses,
        sponsorship=SponsorshipType.GOVERNMENT,
        status=RegistrationStatus.REGISTERED,
    )

    with pytest.raises(InvalidStateTransitionError):
        await reg_service.submit_cost_sharing_form(
            registration_id=reg.id, student_user_id=gov_student.user_id,
        )
