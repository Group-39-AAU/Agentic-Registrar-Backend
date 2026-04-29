"""
Track A — AddDropService integration tests.

Drives the service through every branch of the AddDropRequest
lifecycle: PENDING → APPROVED → APPLIED, PENDING → DENIED, and
DENIED → OVERRIDDEN → APPLIED via officer override.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.modules.course.agents import EnrollmentAdjustmentAgent
from app.modules.course.exceptions import (
    AdjustmentDeniedError, EntityNotFoundError,
    InvalidAdjustmentRequestError, UnauthorizedActorError,
)
from app.modules.course.models import (
    AddDropRequest, Course, CourseOffering, Registration, RegistrationCourse,
    Section,
)
from app.modules.course.service import AddDropService
from app.modules.course.services import PayMock
from app.shared.enums import (
    AddDropAction, AddDropRequestStatus, RegistrationStatus,
    SponsorshipType, UserRole,
)


# ── Fixtures ─────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def isolated_pay_mock() -> PayMock:
    return PayMock()


@pytest_asyncio.fixture
async def add_drop_service(async_session, isolated_pay_mock) -> AddDropService:
    agent = EnrollmentAdjustmentAgent(
        agent_id="AGENT_EAA_TEST",
        payment_service=isolated_pay_mock,
    )
    return AddDropService(async_session, adjustment_agent=agent)


@pytest_asyncio.fixture
async def cs_courses(async_session) -> list[Course]:
    courses = [
        Course(code="CS101", title="Intro", credit_hours=4, semester=1,
               department="Computer Science"),
        Course(code="CS201", title="Data Structures", credit_hours=4, semester=2,
               department="Computer Science"),
        Course(code="CS301", title="Algorithms", credit_hours=4, semester=3,
               department="Computer Science"),
    ]
    async_session.add_all(courses)
    await async_session.flush()
    return courses


@pytest_asyncio.fixture
async def extra_course(async_session) -> Course:
    """A 4-ECTS course not yet in the registration — target for ADD."""
    c = Course(code="MATH101", title="Calc I", credit_hours=4, semester=1,
               department="Mathematics")
    async_session.add(c)
    await async_session.flush()
    return c


@pytest_asyncio.fixture
async def extra_offering(async_session, extra_course, seeded_term):
    """Offering + section for the extra course so ADD can place a section."""
    offering = CourseOffering(
        course_id=extra_course.id, term_id=seeded_term.id,
        capacity=30, section_count=1,
    )
    async_session.add(offering)
    await async_session.flush()
    section = Section(
        offering_id=offering.id, section_code="A",
        room="NB-101", time_slot="MON 08:30-10:00, WED 08:30-10:00",
        capacity=30, enrolled_count=0,
    )
    async_session.add(section)
    await async_session.flush()
    return {"offering": offering, "section": section}


@pytest_asyncio.fixture
async def cs_offerings_and_sections(async_session, cs_courses, seeded_term):
    """Offering + one section per CS course."""
    offerings_sections = []
    for c in cs_courses:
        offering = CourseOffering(
            course_id=c.id, term_id=seeded_term.id,
            capacity=30, section_count=1,
        )
        async_session.add(offering)
        await async_session.flush()
        section = Section(
            offering_id=offering.id, section_code="A",
            room=f"NB-10{cs_courses.index(c)+1}",
            time_slot="MON 13:30-15:00, WED 13:30-15:00",
            capacity=30, enrolled_count=1,
        )
        async_session.add(section)
        await async_session.flush()
        offerings_sections.append((offering, section))
    return offerings_sections


@pytest_asyncio.fixture
async def registration_at_floor(
    async_session, seeded_term, seeded_student, cs_courses,
    cs_offerings_and_sections,
) -> Registration:
    """REGISTERED registration carrying 12 ECTS (the floor)."""
    reg = Registration(
        student_id=seeded_student.id, term_id=seeded_term.id,
        status=RegistrationStatus.REGISTERED,
        sponsorship_type=SponsorshipType.SELF_SPONSORED,
    )
    async_session.add(reg)
    await async_session.flush()
    for course, (_offering, section) in zip(cs_courses, cs_offerings_and_sections):
        async_session.add(RegistrationCourse(
            registration_id=reg.id,
            course_id=course.id,
            section_id=section.id,
        ))
    await async_session.flush()
    await async_session.refresh(reg, attribute_names=["courses"])
    return reg


# ── submit_request → APPLIED happy path ─────────────────────────


async def test_submit_ADD_happy_path_reaches_APPLIED(
    async_session, add_drop_service, isolated_pay_mock,
    registration_at_floor, extra_course, extra_offering, seeded_student,
):
    isolated_pay_mock.set_payment_status(
        seeded_student.id, extra_course.id, paid=True,
    )
    request = await add_drop_service.submit_request(
        registration_id=registration_at_floor.id,
        course_id=extra_course.id,
        action=AddDropAction.ADD,
        deadline=date(2099, 1, 1),
        student_user_id=uuid.uuid4(),
    )
    assert request.status == AddDropRequestStatus.APPLIED

    # RegistrationCourse for the new course exists, pinned to the section
    link = (
        await async_session.execute(
            select(RegistrationCourse).where(
                RegistrationCourse.registration_id == registration_at_floor.id,
                RegistrationCourse.course_id == extra_course.id,
            )
        )
    ).scalar_one()
    assert link.section_id == extra_offering["section"].id
    assert link.is_dropped is False

    # Section enrolled_count incremented
    sec = await async_session.get(Section, extra_offering["section"].id)
    assert sec.enrolled_count == 1


async def test_submit_DROP_happy_path_marks_link_dropped(
    async_session, add_drop_service, registration_at_floor, cs_courses,
    cs_offerings_and_sections, extra_course, extra_offering, isolated_pay_mock,
    seeded_student,
):
    """First add a 4-ECTS course (16 total), then drop a course (12 total)."""
    isolated_pay_mock.set_payment_status(
        seeded_student.id, extra_course.id, paid=True,
    )
    await add_drop_service.submit_request(
        registration_id=registration_at_floor.id,
        course_id=extra_course.id,
        action=AddDropAction.ADD,
        deadline=date(2099, 1, 1),
        student_user_id=uuid.uuid4(),
    )

    drop_request = await add_drop_service.submit_request(
        registration_id=registration_at_floor.id,
        course_id=cs_courses[0].id,
        action=AddDropAction.DROP,
        deadline=date(2099, 1, 1),
        student_user_id=uuid.uuid4(),
    )
    assert drop_request.status == AddDropRequestStatus.APPLIED

    link = (
        await async_session.execute(
            select(RegistrationCourse).where(
                RegistrationCourse.registration_id == registration_at_floor.id,
                RegistrationCourse.course_id == cs_courses[0].id,
            )
        )
    ).scalar_one()
    assert link.is_dropped is True

    # Section's enrolled_count decremented
    _offering, section = cs_offerings_and_sections[0]
    refreshed = await async_session.get(Section, section.id)
    assert refreshed.enrolled_count == 0


# ── submit_request → DENIED ─────────────────────────────────────


async def test_submit_DROP_at_floor_raises_AdjustmentDeniedError(
    add_drop_service, registration_at_floor, cs_courses,
):
    """Dropping any course from a 12-ECTS registration breaks the floor."""
    with pytest.raises(AdjustmentDeniedError) as exc_info:
        await add_drop_service.submit_request(
            registration_id=registration_at_floor.id,
            course_id=cs_courses[0].id,
            action=AddDropAction.DROP,
            deadline=date(2099, 1, 1),
            student_user_id=uuid.uuid4(),
        )
    payload = exc_info.value.payload
    assert payload["approved"] is False
    assert any("floor" in r.lower() for r in payload["reasons"])


async def test_denied_request_persists_with_status_DENIED_and_reason(
    async_session, add_drop_service, registration_at_floor, cs_courses,
):
    with pytest.raises(AdjustmentDeniedError):
        await add_drop_service.submit_request(
            registration_id=registration_at_floor.id,
            course_id=cs_courses[0].id,
            action=AddDropAction.DROP,
            deadline=date(2099, 1, 1),
            student_user_id=uuid.uuid4(),
        )
    await async_session.commit()
    rows = (
        await async_session.execute(
            select(AddDropRequest).where(
                AddDropRequest.registration_id == registration_at_floor.id,
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == AddDropRequestStatus.DENIED
    assert rows[0].reason and "floor" in rows[0].reason.lower()


async def test_submit_rejects_non_REGISTERED_registration(
    async_session, add_drop_service, seeded_term, seeded_student, cs_courses,
):
    reg = Registration(
        student_id=seeded_student.id, term_id=seeded_term.id,
        status=RegistrationStatus.PAYMENT_HOLD,
        sponsorship_type=SponsorshipType.SELF_SPONSORED,
    )
    async_session.add(reg)
    await async_session.flush()

    with pytest.raises(InvalidAdjustmentRequestError):
        await add_drop_service.submit_request(
            registration_id=reg.id,
            course_id=cs_courses[0].id,
            action=AddDropAction.DROP,
            deadline=date(2099, 1, 1),
            student_user_id=uuid.uuid4(),
        )


# ── officer_override ────────────────────────────────────────────


async def test_officer_override_flips_DENIED_to_APPLIED(
    async_session, add_drop_service, registration_at_floor, cs_courses,
    cs_offerings_and_sections,
):
    with pytest.raises(AdjustmentDeniedError):
        await add_drop_service.submit_request(
            registration_id=registration_at_floor.id,
            course_id=cs_courses[0].id,
            action=AddDropAction.DROP,
            deadline=date(2099, 1, 1),
            student_user_id=uuid.uuid4(),
        )
    await async_session.commit()

    denied = (
        await async_session.execute(
            select(AddDropRequest).where(
                AddDropRequest.registration_id == registration_at_floor.id,
            )
        )
    ).scalar_one()
    assert denied.status == AddDropRequestStatus.DENIED

    officer_id = uuid.uuid4()
    overridden = await add_drop_service.officer_override(
        request_id=denied.id,
        officer_role=UserRole.REGISTRAR_OFFICER,
        officer_id=officer_id,
        justification="medical exemption documented in case file 2026-04-29",
    )
    assert overridden.status == AddDropRequestStatus.APPLIED
    assert overridden.override_by_id == officer_id
    assert overridden.override_justification.startswith("medical exemption")


async def test_officer_override_rejects_student_role(
    async_session, add_drop_service, registration_at_floor, cs_courses,
):
    with pytest.raises(AdjustmentDeniedError):
        await add_drop_service.submit_request(
            registration_id=registration_at_floor.id,
            course_id=cs_courses[0].id,
            action=AddDropAction.DROP,
            deadline=date(2099, 1, 1),
            student_user_id=uuid.uuid4(),
        )
    await async_session.commit()

    denied = (
        await async_session.execute(
            select(AddDropRequest).where(
                AddDropRequest.registration_id == registration_at_floor.id,
            )
        )
    ).scalar_one()
    with pytest.raises(UnauthorizedActorError):
        await add_drop_service.officer_override(
            request_id=denied.id,
            officer_role=UserRole.STUDENT,
            officer_id=uuid.uuid4(),
            justification="not allowed",
        )


async def test_officer_override_rejects_empty_justification(
    async_session, add_drop_service, registration_at_floor, cs_courses,
):
    with pytest.raises(AdjustmentDeniedError):
        await add_drop_service.submit_request(
            registration_id=registration_at_floor.id,
            course_id=cs_courses[0].id,
            action=AddDropAction.DROP,
            deadline=date(2099, 1, 1),
            student_user_id=uuid.uuid4(),
        )
    await async_session.commit()

    denied = (
        await async_session.execute(
            select(AddDropRequest).where(
                AddDropRequest.registration_id == registration_at_floor.id,
            )
        )
    ).scalar_one()
    with pytest.raises(InvalidAdjustmentRequestError):
        await add_drop_service.officer_override(
            request_id=denied.id,
            officer_role=UserRole.REGISTRAR_OFFICER,
            officer_id=uuid.uuid4(),
            justification="   ",
        )


async def test_officer_override_rejects_already_APPLIED_request(
    async_session, add_drop_service, isolated_pay_mock,
    registration_at_floor, extra_course, extra_offering, seeded_student,
):
    isolated_pay_mock.set_payment_status(
        seeded_student.id, extra_course.id, paid=True,
    )
    request = await add_drop_service.submit_request(
        registration_id=registration_at_floor.id,
        course_id=extra_course.id,
        action=AddDropAction.ADD,
        deadline=date(2099, 1, 1),
        student_user_id=uuid.uuid4(),
    )
    assert request.status == AddDropRequestStatus.APPLIED

    with pytest.raises(InvalidAdjustmentRequestError):
        await add_drop_service.officer_override(
            request_id=request.id,
            officer_role=UserRole.REGISTRAR_OFFICER,
            officer_id=uuid.uuid4(),
            justification="cannot override an applied request",
        )


# ── reads ───────────────────────────────────────────────────────


async def test_list_for_registration_returns_requests(
    async_session, add_drop_service, isolated_pay_mock,
    registration_at_floor, extra_course, extra_offering, seeded_student,
):
    isolated_pay_mock.set_payment_status(
        seeded_student.id, extra_course.id, paid=True,
    )
    await add_drop_service.submit_request(
        registration_id=registration_at_floor.id,
        course_id=extra_course.id,
        action=AddDropAction.ADD,
        deadline=date(2099, 1, 1),
        student_user_id=uuid.uuid4(),
    )
    rows = await add_drop_service.list_for_registration(
        registration_at_floor.id,
    )
    assert len(rows) == 1
