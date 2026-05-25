"""
Track A — collapsed one-shot registration endpoint tests.

Drives ``RegistrationService.select_courses_and_submit`` (and its
``POST /api/v1/courses/me/register`` HTTP wrapper) through every
branch:

  - Happy path: brand-new student, fresh term, full curriculum →
    REGISTERED with a populated compliance result.
  - Reconciliation: a previous bounced submit left the registration
    in REGISTRATION_OPEN with one course; calling again with a
    different course list rebuilds the join table to match exactly.
  - Idempotent retry while in REGISTRATION_OPEN.
  - 409 when the registration is already REGISTERED.
  - 404 when an unknown course id is supplied.
  - 409 when the term's window is closed.
  - Empty course list bounces with a clean ComplianceCheckFailedError.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.modules.course.agents import CurriculumComplianceAgent
from app.modules.course.exceptions import (
    ComplianceCheckFailedError, EntityNotFoundError,
    InvalidStateTransitionError, RegistrationWindowClosedError,
)
from app.modules.course.models import (
    Course, Registration, RegistrationCourse,
)
from app.modules.course.service import RegistrationService
from app.modules.course.services import PayMock
from app.shared.enums import RegistrationStatus, SponsorshipType


# ── Fixtures ────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def open_term(async_session, seeded_term):
    seeded_term.is_open = True
    await async_session.flush()
    return seeded_term


@pytest_asyncio.fixture
async def closed_term(async_session, seeded_term):
    seeded_term.is_open = False
    await async_session.flush()
    return seeded_term


@pytest_asyncio.fixture(autouse=True)
async def _populate_seeded_student_sponsorship(async_session, seeded_student):
    """
    The shared ``seeded_student`` fixture leaves
    ``Student.sponsorship_type`` null. ``RegistrationService.create_draft``
    insists it's set (it inherits the registration's sponsorship from
    the student row), so this autouse fixture flips it to
    SELF_SPONSORED for every test in this file.
    """
    seeded_student.sponsorship_type = SponsorshipType.SELF_SPONSORED
    await async_session.flush()
    return seeded_student


@pytest_asyncio.fixture
async def cs_courses(async_session) -> dict[str, Course]:
    """Three CS courses with no prereq edges, each at semester 1.
    Credits sized at 6 each so any 2- or 3-course pick clears the
    12-ECTS floor without breaching the 22-ECTS ceiling."""
    cs101 = Course(
        code="CS101", title="Intro Programming", credit_hours=6,
        semester=1, department="Computer Science",
    )
    cs102 = Course(
        code="CS102", title="Discrete Math", credit_hours=6,
        semester=1, department="Computer Science",
    )
    cs103 = Course(
        code="CS103", title="Engineering Drawing", credit_hours=6,
        semester=1, department="Computer Science",
    )
    async_session.add_all([cs101, cs102, cs103])
    await async_session.flush()
    return {"CS101": cs101, "CS102": cs102, "CS103": cs103}


@pytest.fixture
def isolated_pay_mock() -> PayMock:
    return PayMock()


@pytest.fixture
def reg_service(async_session, isolated_pay_mock):
    agent = CurriculumComplianceAgent(
        agent_id="AGENT_CCA_ONESHOT",
        payment_service=isolated_pay_mock,
    )
    return RegistrationService(async_session, compliance_agent=agent)


# ── Happy path ──────────────────────────────────────────────────


async def test_one_shot_register_creates_draft_and_submits(
    reg_service, isolated_pay_mock, open_term, seeded_student, cs_courses,
):
    """Brand-new student, no prior registration → one call, REGISTERED."""
    # Pre-pay every chosen course so the payment check passes
    for course in cs_courses.values():
        isolated_pay_mock.set_payment_status(
            seeded_student.id, course.id, paid=True,
        )

    registration, compliance = await reg_service.select_courses_and_submit(
        student_id=seeded_student.id,
        student_user_id=seeded_student.user_id,
        term_id=open_term.id,
        course_ids=[c.id for c in cs_courses.values()],
    )

    assert registration.status == RegistrationStatus.REGISTERED
    assert compliance["overall_passed"] is True
    assert {rc.course_id for rc in registration.courses if not rc.is_dropped} == {
        c.id for c in cs_courses.values()
    }


# ── Reconciliation ──────────────────────────────────────────────


async def test_one_shot_register_reconciles_previously_added_courses(
    reg_service, isolated_pay_mock, open_term, seeded_student,
    cs_courses, async_session,
):
    """
    The student previously created a draft and bounced. On retry they
    submit a different course list — the registration's RegistrationCourse
    rows are reconciled so the final set matches exactly.
    """
    # Set up the bounced state by hand: registration in REGISTRATION_OPEN
    # with two courses, only one of which the student wants this time.
    reg = await reg_service.create_draft(seeded_student.id, open_term.id)
    await reg_service.add_course_to_draft(reg.id, cs_courses["CS101"].id)
    await reg_service.add_course_to_draft(reg.id, cs_courses["CS102"].id)

    # Retry with a different list: keep CS101, drop CS102, add CS103.
    target = {cs_courses["CS101"].id, cs_courses["CS103"].id}
    for cid in target:
        isolated_pay_mock.set_payment_status(
            seeded_student.id, cid, paid=True,
        )

    registration, compliance = await reg_service.select_courses_and_submit(
        student_id=seeded_student.id,
        student_user_id=seeded_student.user_id,
        term_id=open_term.id,
        course_ids=list(target),
    )

    assert registration.status == RegistrationStatus.REGISTERED
    assert compliance["overall_passed"] is True
    final_set = {
        rc.course_id for rc in registration.courses if not rc.is_dropped
    }
    assert final_set == target

    # Belt-and-braces: confirm the join table actually has only
    # the target courses (no orphaned rows).
    rows = (
        await async_session.execute(
            select(RegistrationCourse).where(
                RegistrationCourse.registration_id == registration.id,
            )
        )
    ).scalars().all()
    assert {r.course_id for r in rows} == target


# ── Already-finalised guard ─────────────────────────────────────


async def test_one_shot_register_blocks_when_already_registered(
    reg_service, isolated_pay_mock, open_term, seeded_student, cs_courses,
):
    for course in cs_courses.values():
        isolated_pay_mock.set_payment_status(
            seeded_student.id, course.id, paid=True,
        )

    # First call goes through to REGISTERED
    await reg_service.select_courses_and_submit(
        student_id=seeded_student.id,
        student_user_id=seeded_student.user_id,
        term_id=open_term.id,
        course_ids=[cs_courses["CS101"].id, cs_courses["CS102"].id],
    )

    # Second call — registration is already REGISTERED → 409
    with pytest.raises(InvalidStateTransitionError):
        await reg_service.select_courses_and_submit(
            student_id=seeded_student.id,
            student_user_id=seeded_student.user_id,
            term_id=open_term.id,
            course_ids=[cs_courses["CS101"].id],
        )


# ── Validation paths ────────────────────────────────────────────


async def test_unknown_course_id_raises_404_before_any_mutation(
    reg_service, open_term, seeded_student, cs_courses, async_session,
):
    """
    A bogus course id should raise EntityNotFoundError without leaving
    any half-written state behind.
    """
    bogus = uuid.uuid4()
    with pytest.raises(EntityNotFoundError, match="Course"):
        await reg_service.select_courses_and_submit(
            student_id=seeded_student.id,
            student_user_id=seeded_student.user_id,
            term_id=open_term.id,
            course_ids=[cs_courses["CS101"].id, bogus],
        )

    # The draft was created (create_draft was called) but no
    # RegistrationCourse rows were written
    rows = (
        await async_session.execute(select(RegistrationCourse))
    ).scalars().all()
    assert rows == []


async def test_closed_term_rejected_with_window_closed(
    reg_service, closed_term, seeded_student, cs_courses,
):
    with pytest.raises(RegistrationWindowClosedError):
        await reg_service.select_courses_and_submit(
            student_id=seeded_student.id,
            student_user_id=seeded_student.user_id,
            term_id=closed_term.id,
            course_ids=[cs_courses["CS101"].id],
        )


async def test_empty_course_list_bounces_compliance(
    reg_service, open_term, seeded_student,
):
    """
    Submitting with no courses bounces with the compliance load-check
    failure ('no active courses') — the registration is created but
    never finalised.
    """
    with pytest.raises(ComplianceCheckFailedError):
        await reg_service.select_courses_and_submit(
            student_id=seeded_student.id,
            student_user_id=seeded_student.user_id,
            term_id=open_term.id,
            course_ids=[],
        )


# ── Idempotent retry while in REGISTRATION_OPEN ─────────────────


async def test_payment_miss_lands_in_payment_hold(
    reg_service, isolated_pay_mock, open_term, seeded_student, cs_courses,
):
    """
    Payment isn't marked → submit() returns normally but the
    registration sits in PAYMENT_HOLD. A retry from that state must
    409, because the cohort design requires the student to clear the
    hold (cost-sharing form / payment callback) before the next
    attempt — calling /me/register again on a PAYMENT_HOLD row would
    skip the bursar contract.
    """
    course_ids = [cs_courses["CS101"].id, cs_courses["CS102"].id]

    registration, compliance = await reg_service.select_courses_and_submit(
        student_id=seeded_student.id,
        student_user_id=seeded_student.user_id,
        term_id=open_term.id,
        course_ids=course_ids,
    )
    assert registration.status == RegistrationStatus.PAYMENT_HOLD
    assert compliance["overall_passed"] is False
    assert compliance["payment_result"]["passed"] is False

    # Retry without clearing the hold — blocked.
    with pytest.raises(InvalidStateTransitionError):
        await reg_service.select_courses_and_submit(
            student_id=seeded_student.id,
            student_user_id=seeded_student.user_id,
            term_id=open_term.id,
            course_ids=course_ids,
        )


async def test_retry_after_clearing_payment_hold(
    reg_service, isolated_pay_mock, open_term, seeded_student,
    cs_courses, async_session,
):
    """
    First call lands in PAYMENT_HOLD. After payment is marked + the
    hold is cleared (the cost-sharing form / bursar callback's job),
    retrying the same call succeeds and reuses the same Registration
    row.
    """
    course_ids = [cs_courses["CS101"].id, cs_courses["CS102"].id]

    first_registration, _ = await reg_service.select_courses_and_submit(
        student_id=seeded_student.id,
        student_user_id=seeded_student.user_id,
        term_id=open_term.id,
        course_ids=course_ids,
    )
    assert first_registration.status == RegistrationStatus.PAYMENT_HOLD

    # Stand in for the bursar callback / cost-sharing form: mark
    # payments + flip the status back to REGISTRATION_OPEN.
    for cid in course_ids:
        isolated_pay_mock.set_payment_status(seeded_student.id, cid, paid=True)
    first_registration.status = RegistrationStatus.REGISTRATION_OPEN
    await async_session.flush()

    # Retry — same course list — now succeeds, same Registration row.
    second, compliance = await reg_service.select_courses_and_submit(
        student_id=seeded_student.id,
        student_user_id=seeded_student.user_id,
        term_id=open_term.id,
        course_ids=course_ids,
    )
    assert second.id == first_registration.id
    assert second.status == RegistrationStatus.REGISTERED
    assert compliance["overall_passed"] is True
