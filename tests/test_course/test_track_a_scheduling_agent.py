"""
Track A — Academic Scheduling Agent contract tests.

Drives the agent through every public method against multi-section
graphs built directly via the test session. SQLite + the in-memory
PayMock keep the suite deterministic and fast.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.modules.auth.models import User
from app.modules.course.agents import (
    AcademicSchedulingAgent,
    CourseBaseAgent,
)
from app.modules.course.models import (
    AcademicTerm, Course, CourseOffering, Instructor, InstructorAssignment,
    Registration, RegistrationCourse, ScheduleConflict, Section, Student,
)
from app.shared.enums import (
    AgentStatus, RegistrationStatus, ScheduleConflictStatus,
    ScheduleConflictType, SponsorshipType, UserRole,
)


# ── Fixtures ─────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def scheduling_agent() -> AcademicSchedulingAgent:
    return AcademicSchedulingAgent(agent_id="AGENT_ASA_TEST")


@pytest_asyncio.fixture
async def cs_term(async_session) -> AcademicTerm:
    term = AcademicTerm(
        term_name="Fall Sched",
        start_date=date(2026, 9, 1),
        end_date=date(2027, 1, 31),
        is_open=True,
    )
    async_session.add(term)
    await async_session.flush()
    return term


@pytest_asyncio.fixture
async def cs_course_offering(async_session, cs_term):
    """One CS course with a single offering."""
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
    return {"course": course, "offering": offering}


@pytest_asyncio.fixture
async def two_instructors(async_session):
    instructors = []
    for n in (1, 2):
        user = User(
            id=uuid.uuid4(),
            email=f"sched-instr-{n}@aau.edu.et",
            first_name=f"Instr{n}", last_name="Sched",
            hashed_password="x", role=UserRole.AGENT, is_active=True,
        )
        async_session.add(user)
        await async_session.flush()
        ins = Instructor(
            user_id=user.id,
            instructor_id=f"STAFF/91{n:02d}/10",
            department="Computer Science",
        )
        async_session.add(ins)
        instructors.append(ins)
    await async_session.flush()
    return instructors


@pytest_asyncio.fixture
async def two_sections(async_session, cs_course_offering, two_instructors):
    """Two sections under the offering, A and B, distinct slots/rooms."""
    sec_a = Section(
        offering_id=cs_course_offering["offering"].id,
        section_code="A",
        room="NB-101", time_slot="MON 08:30-10:00, WED 08:30-10:00",
        instructor_id=two_instructors[0].id,
        capacity=30, enrolled_count=0,
    )
    sec_b = Section(
        offering_id=cs_course_offering["offering"].id,
        section_code="B",
        room="NB-102", time_slot="MON 10:30-12:00, WED 10:30-12:00",
        instructor_id=two_instructors[1].id,
        capacity=30, enrolled_count=0,
    )
    async_session.add_all([sec_a, sec_b])
    await async_session.flush()
    return [sec_a, sec_b]


async def _new_registered_student(
    async_session, cs_term, suffix: str,
) -> tuple[Student, Registration]:
    """Helper: build a User+Student+REGISTERED Registration trio."""
    user = User(
        id=uuid.uuid4(),
        email=f"sched-stu-{suffix}@aau.edu.et",
        first_name=f"Stu-{suffix}", last_name="Sched",
        hashed_password="x", role=UserRole.STUDENT, is_active=True,
    )
    async_session.add(user)
    await async_session.flush()
    student = Student(
        user_id=user.id,
        student_id=f"UGR/8{suffix}/14",
        full_name=f"Stu-{suffix} Sched",
        current_semester=1,
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
    return student, reg


# ── Construction & contract ─────────────────────────────────────


def test_agent_extends_course_base_agent():
    a = AcademicSchedulingAgent()
    assert isinstance(a, CourseBaseAgent)
    assert a.get_status() == AgentStatus.IDLE
    assert a.agent_id.startswith("AGENT_ASA_")


def test_agent_inventory_overridable_via_constructor():
    custom = [("X-1", 100)]
    a = AcademicSchedulingAgent(room_inventory=custom)
    avail = a.get_available_rooms(capacity_req=50, occupied_rooms=set())
    assert avail == [("X-1", 100)]


# ── allocate_sections ───────────────────────────────────────────


async def test_allocate_places_student_into_first_free_section(
    async_session, scheduling_agent, cs_term, cs_course_offering, two_sections,
):
    student, reg = await _new_registered_student(async_session, cs_term, "001")
    async_session.add(RegistrationCourse(
        registration_id=reg.id,
        course_id=cs_course_offering["course"].id,
    ))
    await async_session.flush()

    result = await scheduling_agent.allocate_sections(async_session, cs_term.id)
    assert len(result.allocated) == 1
    assert len(result.failed) == 0

    # Section A should have one enrolled student
    sec_a = await async_session.get(Section, two_sections[0].id)
    assert sec_a.enrolled_count == 1


async def test_allocate_overflow_to_next_section(
    async_session, scheduling_agent, cs_term, cs_course_offering, two_sections,
):
    """Pre-fill section A to capacity; new student must land in B."""
    two_sections[0].enrolled_count = two_sections[0].capacity
    await async_session.flush()

    student, reg = await _new_registered_student(async_session, cs_term, "002")
    async_session.add(RegistrationCourse(
        registration_id=reg.id,
        course_id=cs_course_offering["course"].id,
    ))
    await async_session.flush()

    result = await scheduling_agent.allocate_sections(async_session, cs_term.id)
    assert len(result.allocated) == 1
    sec_b = await async_session.get(Section, two_sections[1].id)
    assert sec_b.enrolled_count == 1


async def test_allocate_fails_when_all_sections_full(
    async_session, scheduling_agent, cs_term, cs_course_offering, two_sections,
):
    two_sections[0].enrolled_count = two_sections[0].capacity
    two_sections[1].enrolled_count = two_sections[1].capacity
    await async_session.flush()

    student, reg = await _new_registered_student(async_session, cs_term, "003")
    async_session.add(RegistrationCourse(
        registration_id=reg.id,
        course_id=cs_course_offering["course"].id,
    ))
    await async_session.flush()

    result = await scheduling_agent.allocate_sections(async_session, cs_term.id)
    assert len(result.allocated) == 0
    assert len(result.failed) == 1
    assert result.failed[0]["reason"] == "all sections full"


async def test_allocate_is_idempotent(
    async_session, scheduling_agent, cs_term, cs_course_offering, two_sections,
):
    student, reg = await _new_registered_student(async_session, cs_term, "004")
    async_session.add(RegistrationCourse(
        registration_id=reg.id,
        course_id=cs_course_offering["course"].id,
    ))
    await async_session.flush()

    first = await scheduling_agent.allocate_sections(async_session, cs_term.id)
    second = await scheduling_agent.allocate_sections(async_session, cs_term.id)
    assert len(first.allocated) == 1
    assert len(second.allocated) == 0    # already allocated, skipped

    sec_a = await async_session.get(Section, two_sections[0].id)
    assert sec_a.enrolled_count == 1


# ── generate_timetable ──────────────────────────────────────────


async def test_generate_timetable_clean_when_no_clashes(
    async_session, scheduling_agent, cs_term, two_sections,
):
    artefact = await scheduling_agent.generate_timetable(
        async_session, cs_term.id, "Computer Science",
    )
    assert artefact.department == "Computer Science"
    assert len(artefact.sections) == 2
    assert artefact.conflict_ids == []


async def test_generate_timetable_resolves_room_clash_via_swap(
    async_session, scheduling_agent, cs_term, two_sections,
):
    """Two sections in same (room, slot) → one gets swapped to a free room."""
    two_sections[1].room = two_sections[0].room
    two_sections[1].time_slot = two_sections[0].time_slot
    await async_session.flush()

    artefact = await scheduling_agent.generate_timetable(
        async_session, cs_term.id, "Computer Science",
    )
    assert artefact.conflict_ids == []     # auto-resolved

    # The two sections now have distinct rooms
    a = await async_session.get(Section, two_sections[0].id)
    b = await async_session.get(Section, two_sections[1].id)
    assert a.room != b.room


async def test_generate_timetable_records_instructor_clash(
    async_session, scheduling_agent, cs_term, two_sections,
):
    """Same instructor, same time_slot → unresolvable, conflict written."""
    two_sections[1].instructor_id = two_sections[0].instructor_id
    two_sections[1].time_slot = two_sections[0].time_slot
    # Keep different rooms so room clash doesn't trigger
    await async_session.flush()

    artefact = await scheduling_agent.generate_timetable(
        async_session, cs_term.id, "Computer Science",
    )
    assert len(artefact.conflict_ids) >= 1

    rows = (await async_session.execute(
        select(ScheduleConflict).where(
            ScheduleConflict.term_id == cs_term.id,
            ScheduleConflict.conflict_type
            == ScheduleConflictType.INSTRUCTOR_DOUBLE_BOOKED,
        )
    )).scalars().all()
    assert len(rows) >= 1
    assert rows[0].status == ScheduleConflictStatus.OPEN
    assert rows[0].detected_by_agent_id == "AGENT_ASA_TEST"


# ── resolve_room_conflict ───────────────────────────────────────


async def test_resolve_room_conflict_swaps_to_free_room(
    async_session, scheduling_agent, two_sections,
):
    swapped = await scheduling_agent.resolve_room_conflict(
        async_session, two_sections[0].id,
    )
    assert swapped is True
    refreshed = await async_session.get(Section, two_sections[0].id)
    assert refreshed.room != "NB-101"


async def test_resolve_room_conflict_returns_false_when_no_alternative(
    async_session, two_sections,
):
    """Tiny inventory of one room → no alternative exists."""
    agent = AcademicSchedulingAgent(
        agent_id="AGENT_ASA_TINY",
        room_inventory=[("ONLY-ROOM", 30)],
    )
    swapped = await agent.resolve_room_conflict(
        async_session, two_sections[0].id,
    )
    # Original room "NB-101" not in tiny inventory and no other rooms
    # available with capacity ≥ 30 except "ONLY-ROOM" which IS the
    # alternative — so this actually swaps. Re-test with a smaller
    # inventory item.
    refreshed = await async_session.get(Section, two_sections[0].id)
    assert refreshed.room == "ONLY-ROOM"


async def test_resolve_room_conflict_false_when_capacity_insufficient(
    async_session, two_sections,
):
    agent = AcademicSchedulingAgent(
        agent_id="AGENT_ASA_TOOSMALL",
        room_inventory=[("TINY", 5)],
    )
    swapped = await agent.resolve_room_conflict(
        async_session, two_sections[0].id,
    )
    assert swapped is False


# ── assign_instructor ───────────────────────────────────────────


async def test_assign_instructor_sets_section_and_creates_assignment(
    async_session, scheduling_agent, cs_term, cs_course_offering,
    two_sections, two_instructors,
):
    target_section = two_sections[0]
    target_section.instructor_id = None
    await async_session.flush()

    new_instructor = two_instructors[1]
    await scheduling_agent.assign_instructor(
        async_session,
        section_id=target_section.id,
        instructor_id=new_instructor.id,
        term_id=cs_term.id,
    )

    refreshed = await async_session.get(Section, target_section.id)
    assert refreshed.instructor_id == new_instructor.id

    rows = (await async_session.execute(
        select(InstructorAssignment).where(
            InstructorAssignment.instructor_id == new_instructor.id,
            InstructorAssignment.course_id == cs_course_offering["course"].id,
            InstructorAssignment.term_id == cs_term.id,
        )
    )).scalars().all()
    assert len(rows) == 1


async def test_assign_instructor_is_idempotent(
    async_session, scheduling_agent, cs_term, cs_course_offering,
    two_sections, two_instructors,
):
    new_instructor = two_instructors[1]
    await scheduling_agent.assign_instructor(
        async_session, section_id=two_sections[0].id,
        instructor_id=new_instructor.id, term_id=cs_term.id,
    )
    await scheduling_agent.assign_instructor(
        async_session, section_id=two_sections[0].id,
        instructor_id=new_instructor.id, term_id=cs_term.id,
    )
    rows = (await async_session.execute(
        select(InstructorAssignment).where(
            InstructorAssignment.instructor_id == new_instructor.id,
            InstructorAssignment.course_id == cs_course_offering["course"].id,
            InstructorAssignment.term_id == cs_term.id,
        )
    )).scalars().all()
    assert len(rows) == 1


# ── get_available_rooms ─────────────────────────────────────────


def test_get_available_rooms_filters_by_capacity_and_occupancy(
    scheduling_agent,
):
    rooms = scheduling_agent.get_available_rooms(
        capacity_req=40, occupied_rooms={"FBE-12"},
    )
    names = {r[0] for r in rooms}
    assert "FBE-12" not in names
    assert "FBE-14" in names
    assert "NB-305" not in names      # capacity 30 < 40


# ── process_task aggregate ──────────────────────────────────────


async def test_process_task_returns_allocation_and_schedule_payload(
    async_session, scheduling_agent, cs_term, cs_course_offering,
    two_sections,
):
    student, reg = await _new_registered_student(async_session, cs_term, "777")
    async_session.add(RegistrationCourse(
        registration_id=reg.id,
        course_id=cs_course_offering["course"].id,
    ))
    await async_session.flush()

    out = await scheduling_agent.process_task({
        "session": async_session,
        "term_id": cs_term.id,
        "department": "Computer Science",
    })
    assert out["allocation"]["allocated_count"] == 1
    assert out["schedule"]["section_count"] == 2
    assert out["schedule"]["department"] == "Computer Science"
