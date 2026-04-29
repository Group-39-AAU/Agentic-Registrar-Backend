"""
Track A — Scheduling service integration tests.

Drives the SchedulingService through the officer's generate path
and the student / instructor read views without going through HTTP.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.modules.auth.models import User
from app.modules.course.agents import AcademicSchedulingAgent
from app.modules.course.exceptions import (
    EntityNotFoundError, UnauthorizedActorError,
)
from app.modules.course.models import (
    AcademicTerm, Course, CourseOffering, Instructor, Registration,
    RegistrationCourse, ScheduleConflict, Section, Student,
)
from app.modules.course.service import SchedulingService
from app.shared.enums import (
    RegistrationStatus, ScheduleConflictStatus, SponsorshipType, UserRole,
)


# ── Fixtures ─────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def sched_service(async_session) -> SchedulingService:
    """Service injected with a deterministic agent_id for log assertions."""
    agent = AcademicSchedulingAgent(agent_id="AGENT_ASA_TEST")
    return SchedulingService(async_session, scheduling_agent=agent)


@pytest_asyncio.fixture
async def cs_term(async_session) -> AcademicTerm:
    term = AcademicTerm(
        term_name="Sched Term",
        start_date=date(2026, 9, 1),
        end_date=date(2027, 1, 31),
        is_open=True,
    )
    async_session.add(term)
    await async_session.flush()
    return term


@pytest_asyncio.fixture
async def cs_offering_with_sections(async_session, cs_term):
    """One CS course, one offering, two sections under it."""
    course = Course(
        code="CS101", title="Intro", credit_hours=4, semester=1,
        department="Computer Science",
    )
    async_session.add(course)
    await async_session.flush()
    offering = CourseOffering(
        course_id=course.id, term_id=cs_term.id,
        capacity=60, section_count=2,
    )
    async_session.add(offering)
    await async_session.flush()

    user = User(
        id=uuid.uuid4(), email="instr-sched@aau.edu.et",
        first_name="I", last_name="Sched",
        hashed_password="x", role=UserRole.AGENT, is_active=True,
    )
    async_session.add(user)
    await async_session.flush()
    instructor = Instructor(
        user_id=user.id, instructor_id="STAFF/9911/10",
        department="Computer Science",
    )
    async_session.add(instructor)
    await async_session.flush()

    sec_a = Section(
        offering_id=offering.id, section_code="A",
        room="NB-101", time_slot="MON 08:30-10:00, WED 08:30-10:00",
        instructor_id=instructor.id, capacity=30, enrolled_count=0,
    )
    sec_b = Section(
        offering_id=offering.id, section_code="B",
        room="NB-102", time_slot="MON 10:30-12:00, WED 10:30-12:00",
        instructor_id=instructor.id, capacity=30, enrolled_count=0,
    )
    async_session.add_all([sec_a, sec_b])
    await async_session.flush()
    return {
        "course": course, "offering": offering, "instructor": instructor,
        "sections": [sec_a, sec_b],
    }


async def _new_registered_student(
    async_session, cs_term, suffix: str, courses: list[Course],
):
    """Build User+Student+REGISTERED Registration with given courses."""
    user = User(
        id=uuid.uuid4(),
        email=f"sched-stu-{suffix}@aau.edu.et",
        first_name=f"Stu-{suffix}", last_name="Sched",
        hashed_password="x", role=UserRole.STUDENT, is_active=True,
    )
    async_session.add(user)
    await async_session.flush()
    student = Student(
        user_id=user.id, student_id=f"UGR/9{suffix}/14",
        full_name=f"Stu-{suffix}", current_semester=1,
    )
    async_session.add(student)
    await async_session.flush()
    reg = Registration(
        student_id=student.id, term_id=cs_term.id,
        status=RegistrationStatus.REGISTERED,
        sponsorship_type=SponsorshipType.SELF_SPONSORED,
    )
    async_session.add(reg)
    await async_session.flush()
    for c in courses:
        async_session.add(RegistrationCourse(
            registration_id=reg.id, course_id=c.id,
        ))
    await async_session.flush()
    return student, reg


# ── Officer generate ─────────────────────────────────────────────


async def test_officer_generate_returns_allocation_and_schedule(
    async_session, sched_service, cs_term, cs_offering_with_sections,
):
    student, _ = await _new_registered_student(
        async_session, cs_term, "001", [cs_offering_with_sections["course"]],
    )

    payload = await sched_service.generate_schedule(
        term_id=cs_term.id,
        department="Computer Science",
        officer_role=UserRole.REGISTRAR_OFFICER,
        officer_id=uuid.uuid4(),
    )
    assert payload["allocation"]["allocated_count"] == 1
    assert payload["schedule"]["section_count"] == 2
    assert payload["schedule"]["department"] == "Computer Science"


async def test_officer_generate_rejects_student_role(
    sched_service, cs_term,
):
    with pytest.raises(UnauthorizedActorError):
        await sched_service.generate_schedule(
            term_id=cs_term.id,
            department="Computer Science",
            officer_role=UserRole.STUDENT,
            officer_id=uuid.uuid4(),
        )


async def test_officer_generate_404_on_unknown_term(sched_service):
    with pytest.raises(EntityNotFoundError):
        await sched_service.generate_schedule(
            term_id=uuid.uuid4(),
            department="Computer Science",
            officer_role=UserRole.REGISTRAR_OFFICER,
            officer_id=uuid.uuid4(),
        )


async def test_officer_generate_records_instructor_clash(
    async_session, sched_service, cs_term, cs_offering_with_sections,
):
    """Same instructor, same slot in both sections → conflict row written."""
    secs = cs_offering_with_sections["sections"]
    secs[1].time_slot = secs[0].time_slot     # collide
    await async_session.flush()

    payload = await sched_service.generate_schedule(
        term_id=cs_term.id,
        department="Computer Science",
        officer_role=UserRole.REGISTRAR_OFFICER,
        officer_id=uuid.uuid4(),
    )
    assert payload["schedule"]["conflict_count"] >= 1

    rows = (
        await async_session.execute(
            select(ScheduleConflict).where(ScheduleConflict.term_id == cs_term.id)
        )
    ).scalars().all()
    assert len(rows) >= 1


# ── list_open_conflicts ──────────────────────────────────────────


async def test_list_open_conflicts_returns_only_OPEN_rows(
    async_session, sched_service, cs_term, cs_offering_with_sections,
):
    secs = cs_offering_with_sections["sections"]
    secs[1].time_slot = secs[0].time_slot
    await async_session.flush()

    await sched_service.generate_schedule(
        term_id=cs_term.id,
        department="Computer Science",
        officer_role=UserRole.REGISTRAR_OFFICER,
        officer_id=uuid.uuid4(),
    )

    open_rows = await sched_service.list_open_conflicts(
        term_id=cs_term.id, officer_role=UserRole.REGISTRAR_OFFICER,
    )
    assert all(c.status == ScheduleConflictStatus.OPEN for c in open_rows)
    assert len(open_rows) >= 1


async def test_list_open_conflicts_rejects_student(
    sched_service, cs_term,
):
    with pytest.raises(UnauthorizedActorError):
        await sched_service.list_open_conflicts(
            term_id=cs_term.id, officer_role=UserRole.STUDENT,
        )


# ── Read views ──────────────────────────────────────────────────


async def test_student_timetable_returns_only_allocated_courses(
    async_session, sched_service, cs_term, cs_offering_with_sections,
):
    student, reg = await _new_registered_student(
        async_session, cs_term, "100", [cs_offering_with_sections["course"]],
    )

    # Before allocation: timetable empty
    before = await sched_service.get_student_timetable(student.id, cs_term.id)
    assert before == []

    await sched_service.generate_schedule(
        term_id=cs_term.id,
        department="Computer Science",
        officer_role=UserRole.REGISTRAR_OFFICER,
        officer_id=uuid.uuid4(),
    )

    after = await sched_service.get_student_timetable(student.id, cs_term.id)
    assert len(after) == 1
    entry = after[0]
    assert entry["course_code"] == "CS101"
    assert entry["section_code"] in {"A", "B"}
    assert entry["time_slot"] is not None


async def test_instructor_timetable_lists_assigned_sections(
    async_session, sched_service, cs_term, cs_offering_with_sections,
):
    instructor = cs_offering_with_sections["instructor"]
    rows = await sched_service.get_instructor_timetable(
        instructor.id, cs_term.id,
    )
    # Two sections both assigned to this instructor in the fixture
    assert len(rows) == 2
    assert {r["section_code"] for r in rows} == {"A", "B"}
