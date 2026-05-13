"""
Track A — registration workflow integration tests.

Drives the RegistrationService through every branch of the SDS
Figure 39 state machine without going through HTTP. The tests pass
an injected CurriculumComplianceAgent backed by an isolated PayMock
so each test owns its compliance state.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

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
    Course, CoursePrerequisite, Registration, RegistrationCourse,
    RegistrationStatusHistory,
)
from app.modules.course.service import RegistrationService, TermService
from app.modules.course.services import PayMock
from app.shared.enums import (
    RegistrationStatus, SponsorshipType, UserRole,
)


@pytest_asyncio.fixture
async def open_term(async_session, seeded_term):
    """Mark the seeded Phase-0 term as open so tests can register against it."""
    seeded_term.is_open = True
    await async_session.flush()
    return seeded_term


@pytest_asyncio.fixture
async def closed_term(async_session, seeded_term):
    seeded_term.is_open = False
    await async_session.flush()
    return seeded_term


@pytest_asyncio.fixture
async def cs_chain_with_payments(async_session) -> dict[str, Course]:
    """CS101 -> CS201 prereq chain."""
    cs101 = Course(
        code="CS101", title="Intro", credit_hours=4, semester=1,
        department="Computer Science",
    )
    cs201 = Course(
        code="CS201", title="Data Structures", credit_hours=4, semester=2,
        department="Computer Science",
    )
    async_session.add_all([cs101, cs201])
    await async_session.flush()
    async_session.add(
        CoursePrerequisite(course_id=cs201.id, prerequisite_course_id=cs101.id)
    )
    await async_session.flush()
    return {"CS101": cs101, "CS201": cs201}


@pytest.fixture
def isolated_pay_mock() -> PayMock:
    return PayMock()


@pytest.fixture
def reg_service(async_session, isolated_pay_mock):
    agent = CurriculumComplianceAgent(
        agent_id="AGENT_CCA_TEST",
        payment_service=isolated_pay_mock,
    )
    return RegistrationService(async_session, compliance_agent=agent)


# ── TermService ─────────────────────────────────────────────────


async def test_officer_can_open_and_close_term(async_session, seeded_term):
    seeded_term.is_open = False
    await async_session.flush()
    svc = TermService(async_session)

    opened = await svc.open_window(
        seeded_term.id, UserRole.REGISTRAR_OFFICER, uuid.uuid4(),
    )
    assert opened.is_open is True

    closed = await svc.close_window(
        seeded_term.id, UserRole.REGISTRAR_OFFICER, uuid.uuid4(),
    )
    assert closed.is_open is False


async def test_student_role_cannot_open_term(async_session, seeded_term):
    svc = TermService(async_session)
    with pytest.raises(UnauthorizedActorError):
        await svc.open_window(
            seeded_term.id, UserRole.STUDENT, uuid.uuid4(),
        )


# ── Draft creation ──────────────────────────────────────────────


async def test_create_draft_succeeds_when_window_open(
    reg_service, open_term, seeded_student,
):
    reg = await reg_service.create_draft(
        seeded_student.id, open_term.id, SponsorshipType.SELF_SPONSORED,
    )
    assert reg.status == RegistrationStatus.REGISTRATION_OPEN
    assert reg.sponsorship_type == SponsorshipType.SELF_SPONSORED


async def test_create_draft_fails_when_window_closed(
    reg_service, closed_term, seeded_student,
):
    with pytest.raises(RegistrationWindowClosedError):
        await reg_service.create_draft(
            seeded_student.id, closed_term.id, SponsorshipType.GOVERNMENT,
        )


async def test_create_draft_rejects_duplicate_for_same_term(
    reg_service, open_term, seeded_student,
):
    await reg_service.create_draft(
        seeded_student.id, open_term.id, SponsorshipType.SELF_SPONSORED,
    )
    with pytest.raises(DuplicateRegistrationError):
        await reg_service.create_draft(
            seeded_student.id, open_term.id, SponsorshipType.SELF_SPONSORED,
        )


# ── Add / remove courses on draft ───────────────────────────────


async def test_add_course_to_draft_is_idempotent(
    reg_service, open_term, seeded_student, cs_chain_with_payments,
):
    reg = await reg_service.create_draft(
        seeded_student.id, open_term.id, SponsorshipType.SELF_SPONSORED,
    )
    await reg_service.add_course_to_draft(reg.id, cs_chain_with_payments["CS101"].id)
    await reg_service.add_course_to_draft(reg.id, cs_chain_with_payments["CS101"].id)
    fetched = await reg_service.registrations.get(reg.id)
    assert len(fetched.courses) == 1


async def test_remove_course_from_draft(
    reg_service, open_term, seeded_student, cs_chain_with_payments,
):
    reg = await reg_service.create_draft(
        seeded_student.id, open_term.id, SponsorshipType.SELF_SPONSORED,
    )
    await reg_service.add_course_to_draft(reg.id, cs_chain_with_payments["CS101"].id)
    await reg_service.remove_course_from_draft(
        reg.id, cs_chain_with_payments["CS101"].id,
    )
    fetched = await reg_service.registrations.get(reg.id)
    assert len(fetched.courses) == 0


# ── Submit: the spine ───────────────────────────────────────────


async def test_submit_happy_path_reaches_REGISTERED(
    async_session,
    reg_service,
    isolated_pay_mock,
    open_term,
    seeded_student,
    cs_chain_with_payments,
):
    reg = await reg_service.create_draft(
        seeded_student.id, open_term.id, SponsorshipType.SELF_SPONSORED,
    )
    await reg_service.add_course_to_draft(
        reg.id, cs_chain_with_payments["CS101"].id,
    )

    # Mark CS101 paid
    isolated_pay_mock.set_payment_status(
        seeded_student.id, cs_chain_with_payments["CS101"].id, paid=True,
    )

    finalised, compliance = await reg_service.submit(
        reg.id, student_user_id=seeded_student.user_id,
    )
    assert finalised.status == RegistrationStatus.REGISTERED
    assert finalised.finalised_at is not None
    assert compliance["overall_passed"] is True


async def test_submit_with_payment_missing_lands_in_PAYMENT_HOLD(
    reg_service,
    open_term,
    seeded_student,
    cs_chain_with_payments,
):
    """No PayMock entries → unpaid → PAYMENT_HOLD."""
    reg = await reg_service.create_draft(
        seeded_student.id, open_term.id, SponsorshipType.SELF_SPONSORED,
    )
    await reg_service.add_course_to_draft(
        reg.id, cs_chain_with_payments["CS101"].id,
    )

    finalised, compliance = await reg_service.submit(
        reg.id, student_user_id=seeded_student.user_id,
    )
    assert finalised.status == RegistrationStatus.PAYMENT_HOLD
    assert compliance["overall_passed"] is False
    assert compliance["payment_result"]["passed"] is False


async def test_submit_with_missing_prereq_returns_to_REGISTRATION_OPEN(
    async_session,
    reg_service,
    isolated_pay_mock,
    open_term,
    seeded_student,
    cs_chain_with_payments,
):
    """Registering CS201 without CS101 in completed history must fail."""
    reg = await reg_service.create_draft(
        seeded_student.id, open_term.id, SponsorshipType.SELF_SPONSORED,
    )
    await reg_service.add_course_to_draft(
        reg.id, cs_chain_with_payments["CS201"].id,
    )
    isolated_pay_mock.set_payment_status(
        seeded_student.id, cs_chain_with_payments["CS201"].id, paid=True,
    )

    with pytest.raises(ComplianceCheckFailedError) as exc_info:
        await reg_service.submit(reg.id, student_user_id=seeded_student.user_id)

    payload = exc_info.value.payload
    assert payload["overall_passed"] is False
    assert any(not p["passed"] for p in payload["prereq_results"])

    # Registration is bounced back to draft so the student can fix it.
    refreshed = await reg_service.registrations.get(reg.id)
    assert refreshed.status == RegistrationStatus.REGISTRATION_OPEN


async def test_submit_writes_status_history_for_every_transition(
    async_session,
    reg_service,
    isolated_pay_mock,
    open_term,
    seeded_student,
    cs_chain_with_payments,
):
    reg = await reg_service.create_draft(
        seeded_student.id, open_term.id, SponsorshipType.SELF_SPONSORED,
    )
    await reg_service.add_course_to_draft(
        reg.id, cs_chain_with_payments["CS101"].id,
    )
    isolated_pay_mock.set_payment_status(
        seeded_student.id, cs_chain_with_payments["CS101"].id, paid=True,
    )
    await reg_service.submit(reg.id, student_user_id=seeded_student.user_id)

    history = (
        await async_session.execute(
            select(RegistrationStatusHistory)
            .where(RegistrationStatusHistory.registration_id == reg.id)
            .order_by(RegistrationStatusHistory.created_at.asc())
        )
    ).scalars().all()
    transitions = [h.new_status for h in history]
    # initial draft + 4 agent-driven transitions to reach REGISTERED
    assert transitions == [
        RegistrationStatus.REGISTRATION_OPEN,
        RegistrationStatus.CHECKING_PREREQUISITES,
        RegistrationStatus.CHECKING_PAYMENT,
        RegistrationStatus.VALIDATION_SUCCESS,
        RegistrationStatus.REGISTERED,
    ]
