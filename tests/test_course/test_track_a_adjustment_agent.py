"""
Track A — EnrollmentAdjustmentAgent contract tests.

Drives the agent through every public method against a tight graph
of registration + courses + sections built per-test in SQLite. Each
test owns an isolated PayMock so payment state never leaks.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.modules.course.agents import (
    AdjustmentResult,
    CourseBaseAgent,
    EnrollmentAdjustmentAgent,
    MIN_CREDIT_LOAD_ECTS,
)
from app.modules.course.models import (
    AddDropRequest, Course, CourseOffering, Registration, RegistrationCourse,
    Section,
)
from app.modules.course.services import PayMock
from app.shared.enums import (
    AddDropAction, AddDropRequestStatus, AgentStatus, RegistrationStatus,
    SponsorshipType,
)


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def isolated_pay_mock() -> PayMock:
    return PayMock()


@pytest.fixture
def adjustment_agent(isolated_pay_mock) -> EnrollmentAdjustmentAgent:
    return EnrollmentAdjustmentAgent(
        agent_id="AGENT_EAA_TEST",
        payment_service=isolated_pay_mock,
    )


@pytest_asyncio.fixture
async def cs_courses(async_session) -> list[Course]:
    """Three CS courses, all 4 ECTS each (total 12 if all selected)."""
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
async def heavy_course(async_session) -> Course:
    """A 6-ECTS course used for ceiling tests."""
    c = Course(code="MATH601", title="Heavy", credit_hours=6, semester=1,
               department="Mathematics")
    async_session.add(c)
    await async_session.flush()
    return c


@pytest_asyncio.fixture
async def registration_with_courses(
    async_session, seeded_term, seeded_student, cs_courses,
) -> Registration:
    """Registration carrying the three 4-ECTS CS courses (12 ECTS total)."""
    reg = Registration(
        student_id=seeded_student.id, term_id=seeded_term.id,
        status=RegistrationStatus.REGISTERED,
        sponsorship_type=SponsorshipType.SELF_SPONSORED,
    )
    async_session.add(reg)
    await async_session.flush()
    for c in cs_courses:
        async_session.add(RegistrationCourse(
            registration_id=reg.id, course_id=c.id,
        ))
    await async_session.flush()
    await async_session.refresh(reg, attribute_names=["courses"])
    return reg


def _build_request(
    *, registration_id: uuid.UUID, course_id: uuid.UUID,
    action: AddDropAction, deadline: date,
    target_section_id: uuid.UUID | None = None,
) -> AddDropRequest:
    """Helper: build an AddDropRequest object without committing it."""
    return AddDropRequest(
        registration_id=registration_id,
        course_id=course_id,
        target_section_id=target_section_id,
        action=action,
        deadline_snapshot=deadline,
        status=AddDropRequestStatus.PENDING,
    )


# ── Construction & contract ─────────────────────────────────────


def test_agent_extends_course_base_agent_contract():
    a = EnrollmentAdjustmentAgent()
    assert isinstance(a, CourseBaseAgent)
    assert a.get_status() == AgentStatus.IDLE
    assert a.agent_id.startswith("AGENT_EAA_")


def test_agent_constants_match_sds_invariants():
    assert MIN_CREDIT_LOAD_ECTS == 12        # SDS Table 80 floor


# ── validate_adjustment_window ──────────────────────────────────


def test_validate_window_passes_when_today_before_deadline(adjustment_agent):
    deadline = date(2099, 1, 1)
    result = adjustment_agent.validate_adjustment_window(deadline)
    assert result.passed


def test_validate_window_passes_when_today_equals_deadline(adjustment_agent):
    today = date(2099, 1, 1)
    result = adjustment_agent.validate_adjustment_window(today, today=today)
    assert result.passed


def test_validate_window_fails_when_deadline_passed(adjustment_agent):
    yesterday = date(2099, 1, 1) - timedelta(days=1)
    result = adjustment_agent.validate_adjustment_window(
        yesterday, today=date(2099, 1, 1),
    )
    assert result.passed is False
    assert "passed" in result.reasons[0].lower()


# ── cross_check_payment ─────────────────────────────────────────


def test_cross_check_payment_passes_when_paid(
    adjustment_agent, isolated_pay_mock,
):
    sid, cid = uuid.uuid4(), uuid.uuid4()
    isolated_pay_mock.set_payment_status(sid, cid, paid=True)
    assert adjustment_agent.cross_check_payment(sid, cid).passed


def test_cross_check_payment_fails_when_unpaid(adjustment_agent):
    result = adjustment_agent.cross_check_payment(uuid.uuid4(), uuid.uuid4())
    assert result.passed is False
    assert "outstanding" in result.reasons[0].lower()


# ── update_section_capacity ─────────────────────────────────────


async def test_update_section_capacity_ADD_increments(
    async_session, adjustment_agent, seeded_section,
):
    seeded_section.enrolled_count = 5
    await async_session.flush()
    after = await adjustment_agent.update_section_capacity(
        async_session, seeded_section.id, AddDropAction.ADD,
    )
    assert after.enrolled_count == 6


async def test_update_section_capacity_DROP_decrements(
    async_session, adjustment_agent, seeded_section,
):
    seeded_section.enrolled_count = 5
    await async_session.flush()
    after = await adjustment_agent.update_section_capacity(
        async_session, seeded_section.id, AddDropAction.DROP,
    )
    assert after.enrolled_count == 4


async def test_update_section_capacity_ADD_raises_when_full(
    async_session, adjustment_agent, seeded_section,
):
    seeded_section.enrolled_count = seeded_section.capacity
    await async_session.flush()
    with pytest.raises(ValueError):
        await adjustment_agent.update_section_capacity(
            async_session, seeded_section.id, AddDropAction.ADD,
        )


async def test_update_section_capacity_DROP_clamps_at_zero(
    async_session, adjustment_agent, seeded_section,
):
    seeded_section.enrolled_count = 0
    await async_session.flush()
    after = await adjustment_agent.update_section_capacity(
        async_session, seeded_section.id, AddDropAction.DROP,
    )
    assert after.enrolled_count == 0


# ── process_add_drop ────────────────────────────────────────────


async def test_process_ADD_happy_path(
    async_session, adjustment_agent, isolated_pay_mock,
    registration_with_courses, heavy_course, seeded_student,
):
    """
    Add a 6-ECTS course on top of the 12 ECTS already in the
    registration → 18 ECTS, well under the 22 ECTS ceiling.
    """
    isolated_pay_mock.set_payment_status(
        seeded_student.id, heavy_course.id, paid=True,
    )
    request = _build_request(
        registration_id=registration_with_courses.id,
        course_id=heavy_course.id,
        action=AddDropAction.ADD,
        deadline=date(2099, 1, 1),
    )
    result = await adjustment_agent.process_add_drop(
        async_session, request, registration_with_courses,
    )
    assert result.approved
    assert result.details["new_total"] == 18


async def test_process_ADD_blocked_by_credit_ceiling(
    async_session, adjustment_agent, isolated_pay_mock,
    registration_with_courses,
):
    """
    Pre-load registration with 18 ECTS already, then try to add a
    6-ECTS course → 24 ECTS, breaches the 22 ECTS ceiling.
    """
    # Add a 6-ECTS course to the existing 12 ECTS to reach 18.
    extra = Course(code="MATH601", title="Heavy", credit_hours=6, semester=1,
                   department="Mathematics")
    async_session.add(extra)
    await async_session.flush()
    async_session.add(RegistrationCourse(
        registration_id=registration_with_courses.id, course_id=extra.id,
    ))
    await async_session.flush()
    await async_session.refresh(
        registration_with_courses, attribute_names=["courses"],
    )

    # Now propose adding ANOTHER 6-ECTS course → 24 total.
    breaker = Course(code="MATH602", title="Even Heavier",
                     credit_hours=6, semester=1, department="Mathematics")
    async_session.add(breaker)
    await async_session.flush()

    request = _build_request(
        registration_id=registration_with_courses.id,
        course_id=breaker.id,
        action=AddDropAction.ADD,
        deadline=date(2099, 1, 1),
    )
    result = await adjustment_agent.process_add_drop(
        async_session, request, registration_with_courses,
    )
    assert result.approved is False
    assert "ceiling" in result.reasons[0].lower()
    assert result.details["new_total"] == 24


async def test_process_ADD_blocked_by_missing_payment(
    async_session, adjustment_agent,
    registration_with_courses, heavy_course,
):
    request = _build_request(
        registration_id=registration_with_courses.id,
        course_id=heavy_course.id,
        action=AddDropAction.ADD,
        deadline=date(2099, 1, 1),
    )
    result = await adjustment_agent.process_add_drop(
        async_session, request, registration_with_courses,
    )
    assert result.approved is False
    assert "outstanding" in result.reasons[0].lower()


async def test_process_blocked_by_closed_window(
    async_session, adjustment_agent, isolated_pay_mock,
    registration_with_courses, heavy_course, seeded_student,
):
    isolated_pay_mock.set_payment_status(
        seeded_student.id, heavy_course.id, paid=True,
    )
    request = _build_request(
        registration_id=registration_with_courses.id,
        course_id=heavy_course.id,
        action=AddDropAction.ADD,
        deadline=date(2020, 1, 1),         # long passed
    )
    result = await adjustment_agent.process_add_drop(
        async_session, request, registration_with_courses,
        today=date(2026, 4, 29),
    )
    assert result.approved is False
    assert "deadline" in result.reasons[0].lower()


async def test_process_DROP_blocked_by_credit_floor(
    async_session, adjustment_agent,
    registration_with_courses, cs_courses,
):
    """
    Registration is at exactly 12 ECTS — the floor. Dropping any
    course must fail.
    """
    request = _build_request(
        registration_id=registration_with_courses.id,
        course_id=cs_courses[0].id,
        action=AddDropAction.DROP,
        deadline=date(2099, 1, 1),
    )
    result = await adjustment_agent.process_add_drop(
        async_session, request, registration_with_courses,
    )
    assert result.approved is False
    assert "floor" in result.reasons[0].lower()
    assert result.details["floor"] == MIN_CREDIT_LOAD_ECTS


async def test_process_DROP_happy_path_above_floor(
    async_session, adjustment_agent,
    registration_with_courses, cs_courses, heavy_course,
):
    """
    Bring registration up to 18 ECTS first, then dropping a 4-ECTS
    course leaves 14 ECTS — clears the floor.
    """
    async_session.add(RegistrationCourse(
        registration_id=registration_with_courses.id,
        course_id=heavy_course.id,
    ))
    await async_session.flush()
    await async_session.refresh(
        registration_with_courses, attribute_names=["courses"],
    )

    request = _build_request(
        registration_id=registration_with_courses.id,
        course_id=cs_courses[0].id,
        action=AddDropAction.DROP,
        deadline=date(2099, 1, 1),
    )
    result = await adjustment_agent.process_add_drop(
        async_session, request, registration_with_courses,
    )
    assert result.approved
    assert result.details["new_total"] == 14


# ── notify_adjustment_success ───────────────────────────────────


def test_notify_adjustment_success_returns_payload(adjustment_agent):
    request = _build_request(
        registration_id=uuid.uuid4(),
        course_id=uuid.uuid4(),
        action=AddDropAction.ADD,
        deadline=date(2099, 1, 1),
    )
    request.id = uuid.uuid4()
    payload = adjustment_agent.notify_adjustment_success(uuid.uuid4(), request)
    assert "subject" in payload
    assert "body" in payload
    assert "ADD" in payload["body"]


# ── process_task aggregate ──────────────────────────────────────


async def test_process_task_returns_dict_payload(
    async_session, adjustment_agent, isolated_pay_mock,
    registration_with_courses, heavy_course, seeded_student,
):
    isolated_pay_mock.set_payment_status(
        seeded_student.id, heavy_course.id, paid=True,
    )
    request = _build_request(
        registration_id=registration_with_courses.id,
        course_id=heavy_course.id,
        action=AddDropAction.ADD,
        deadline=date(2099, 1, 1),
    )
    out = await adjustment_agent.process_task({
        "session": async_session,
        "request": request,
        "registration": registration_with_courses,
    })
    assert out["approved"] is True
    assert "new_total" in out["details"]
