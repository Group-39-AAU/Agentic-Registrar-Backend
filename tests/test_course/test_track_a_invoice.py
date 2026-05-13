"""
Track A — tuition invoice tests.

Drives ``RegistrationService.get_invoice`` through:

  - Self-sponsored happy path: amount_due == sum(credit_hours) * rate
  - Government-sponsored: same line items, amount_due == 0
  - Dropped courses excluded from the line items + total
  - Empty registration: zero everything + a clear note
  - Ownership rejection (a different student calling)
  - Unknown registration → 404
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from app.core.config import settings
from app.modules.auth.models import User
from app.modules.course.exceptions import (
    EntityNotFoundError, UnauthorizedActorError,
)
from app.modules.course.models import (
    Course, Registration, RegistrationCourse,
)
from app.modules.course.service import RegistrationService
from app.shared.enums import (
    RegistrationStatus, SponsorshipType, UserRole,
)


# ── Fixtures ────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def cs_courses(async_session) -> dict[str, Course]:
    cs101 = Course(
        code="CS101", title="Intro Programming", credit_hours=4,
        semester=1, department="Computer Science",
    )
    cs102 = Course(
        code="CS102", title="Discrete Math", credit_hours=3,
        semester=1, department="Computer Science",
    )
    cs103 = Course(
        code="CS103", title="Engineering Drawing", credit_hours=3,
        semester=1, department="Computer Science",
    )
    async_session.add_all([cs101, cs102, cs103])
    await async_session.flush()
    return {"CS101": cs101, "CS102": cs102, "CS103": cs103}


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


# ── Self-sponsored ──────────────────────────────────────────────


async def test_self_sponsored_invoice_charges_per_credit_hour(
    async_session, reg_service, seeded_student, seeded_term, cs_courses,
):
    """4 + 3 + 3 = 10 credit hours × 100 birr = 1000 birr due."""
    reg = await _build_registration(
        async_session, seeded_student, seeded_term,
        list(cs_courses.values()),
        sponsorship=SponsorshipType.SELF_SPONSORED,
    )

    invoice = await reg_service.get_invoice(
        registration_id=reg.id,
        student_user_id=seeded_student.user_id,
    )

    rate = settings.FEE_PER_CREDIT_HOUR_BIRR
    expected = 10 * rate
    assert invoice["sponsorship_type"] == SponsorshipType.SELF_SPONSORED
    assert invoice["fee_per_credit_hour"] == rate
    assert invoice["total_credit_hours"] == 10
    assert invoice["gross_total"] == expected
    assert invoice["amount_due"] == expected
    assert invoice["is_government_sponsored"] is False
    assert invoice["payment_required"] is True

    # Line items match the courses one-for-one
    line_codes = {line["course_code"] for line in invoice["lines"]}
    assert line_codes == {"CS101", "CS102", "CS103"}
    cs101_line = next(l for l in invoice["lines"] if l["course_code"] == "CS101")
    assert cs101_line["credit_hours"] == 4
    assert cs101_line["line_total"] == 4 * rate


# ── Government-sponsored ────────────────────────────────────────


async def test_government_sponsored_invoice_shows_zero_amount_due(
    async_session, reg_service, seeded_student, seeded_term, cs_courses,
):
    """Same line items as self-sponsored, but amount_due == 0."""
    reg = await _build_registration(
        async_session, seeded_student, seeded_term,
        list(cs_courses.values()),
        sponsorship=SponsorshipType.GOVERNMENT,
    )

    invoice = await reg_service.get_invoice(
        registration_id=reg.id,
        student_user_id=seeded_student.user_id,
    )

    rate = settings.FEE_PER_CREDIT_HOUR_BIRR
    assert invoice["sponsorship_type"] == SponsorshipType.GOVERNMENT
    assert invoice["total_credit_hours"] == 10
    assert invoice["gross_total"] == 10 * rate    # for transparency
    assert invoice["amount_due"] == 0
    assert invoice["is_government_sponsored"] is True
    assert invoice["payment_required"] is False
    assert "cost-sharing" in invoice["note"].lower()


# ── Dropped courses ─────────────────────────────────────────────


async def test_dropped_courses_excluded_from_invoice(
    async_session, reg_service, seeded_student, seeded_term, cs_courses,
):
    reg = await _build_registration(
        async_session, seeded_student, seeded_term,
        list(cs_courses.values()),
        sponsorship=SponsorshipType.SELF_SPONSORED,
    )
    # Drop CS101 (4 credits)
    for rc in reg.courses:
        if rc.course_id == cs_courses["CS101"].id:
            rc.is_dropped = True
    await async_session.flush()

    invoice = await reg_service.get_invoice(
        registration_id=reg.id,
        student_user_id=seeded_student.user_id,
    )

    rate = settings.FEE_PER_CREDIT_HOUR_BIRR
    # Only CS102 + CS103 remain → 3 + 3 = 6 credits
    assert invoice["total_credit_hours"] == 6
    assert invoice["amount_due"] == 6 * rate
    assert {l["course_code"] for l in invoice["lines"]} == {"CS102", "CS103"}


# ── Empty registration ──────────────────────────────────────────


async def test_empty_registration_returns_zero_invoice_with_note(
    async_session, reg_service, seeded_student, seeded_term,
):
    reg = await _build_registration(
        async_session, seeded_student, seeded_term, [],
        sponsorship=SponsorshipType.SELF_SPONSORED,
    )

    invoice = await reg_service.get_invoice(
        registration_id=reg.id,
        student_user_id=seeded_student.user_id,
    )

    assert invoice["lines"] == []
    assert invoice["total_credit_hours"] == 0
    assert invoice["amount_due"] == 0
    assert invoice["payment_required"] is False
    assert "no active courses" in invoice["note"].lower()


# ── Ownership / 404 ─────────────────────────────────────────────


async def test_invoice_blocks_non_owner(
    async_session, reg_service, seeded_student, seeded_term, cs_courses,
):
    reg = await _build_registration(
        async_session, seeded_student, seeded_term,
        list(cs_courses.values()),
        sponsorship=SponsorshipType.SELF_SPONSORED,
    )
    intruder = User(
        id=uuid.uuid4(),
        email="intruder-invoice@aau.edu.et",
        first_name="In", last_name="Truder",
        hashed_password="x", role=UserRole.STUDENT, is_active=True,
    )
    async_session.add(intruder)
    await async_session.flush()

    with pytest.raises(UnauthorizedActorError):
        await reg_service.get_invoice(
            registration_id=reg.id,
            student_user_id=intruder.id,
        )


async def test_unknown_registration_raises_404(async_session, reg_service):
    with pytest.raises(EntityNotFoundError, match="Registration"):
        await reg_service.get_invoice(
            registration_id=uuid.uuid4(),
            student_user_id=uuid.uuid4(),
        )
