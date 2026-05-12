"""
Track A — cohort-based AcademicSchedulingAgent, department-scoped.

Verifies the per-department contract:

  - allocate_sections groups REGISTERED students in *one* department
    by current_semester and pins each to a Section with a globally-
    unique code (A, B, C, …) and a room from that department's
    Classroom inventory.
  - generate_schedule lays out ClassScheduleSlot rows whose total
    weekly hours per course equal ``course.credit_hours``.
  - Re-runs are idempotent: students already pinned stay put;
    extra capacity is filled before new sections are created.
  - Calls for one department don't touch another department's
    sections or slots.

Tests inject ``room_inventory=`` on the agent to bypass the
``Classroom`` DB query, so each test owns its own room budget
without touching seeded rows.
"""
from __future__ import annotations

import uuid

import pytest_asyncio
from sqlalchemy import select

from app.modules.course.agents import AcademicSchedulingAgent
from app.modules.course.models import (
    ClassScheduleSlot, Course, InstructorAssignment,
    Registration, Section, Student,
)
from app.modules.auth.models import User
from app.shared.enums import (
    EnrollmentStatus, RegistrationStatus, SponsorshipType, UserRole,
)


# ── Tiny per-test helpers ────────────────────────────────────────


_DEFAULT_ROOMS = [("BIG-1", 100), ("MED-1", 40), ("LAB-1", 20)]


def _make_agent(rooms: list[tuple[str, int]] | None = None) -> AcademicSchedulingAgent:
    return AcademicSchedulingAgent(
        room_inventory=rooms if rooms is not None else _DEFAULT_ROOMS,
    )


async def _add_student(
    session, *, semester: int, department: str, email: str, full_name: str,
) -> Student:
    user = User(
        id=uuid.uuid4(), email=email,
        first_name=full_name.split()[0], last_name=full_name.split()[-1],
        hashed_password="x", role=UserRole.STUDENT, is_active=True,
    )
    session.add(user)
    await session.flush()
    student = Student(
        user_id=user.id,
        student_id=f"UGR/{abs(hash(email)) % 9000 + 1000}/14",
        full_name=full_name,
        current_semester=semester,
        department=department,
        enrollment_status=EnrollmentStatus.ACTIVE,
    )
    session.add(student)
    await session.flush()
    return student


async def _register(session, student, term, *, status=RegistrationStatus.REGISTERED):
    reg = Registration(
        student_id=student.id,
        term_id=term.id,
        status=status,
        sponsorship_type=SponsorshipType.SELF_SPONSORED,
    )
    session.add(reg)
    await session.flush()
    return reg


@pytest_asyncio.fixture
async def cs_sem1_course(async_session) -> Course:
    course = Course(
        code="CS101", title="Intro Programming",
        credit_hours=3, semester=1, department="Computer Science",
    )
    async_session.add(course)
    await async_session.flush()
    return course


# ── allocate_sections ────────────────────────────────────────────


async def test_allocate_creates_one_section_when_under_capacity(
    async_session, seeded_term, cs_sem1_course,
):
    s = await _add_student(
        async_session, semester=1, department="Computer Science",
        email="student-a@aau.edu.et", full_name="Student A",
    )
    await _register(async_session, s, seeded_term)

    agent = _make_agent()
    result = await agent.allocate_sections(
        async_session, seeded_term.id, "Computer Science",
    )

    assert len(result.sections_created) == 1
    assert len(result.students_placed) == 1
    sec = (
        await async_session.execute(
            select(Section).where(Section.term_id == seeded_term.id)
        )
    ).scalar_one()
    assert sec.section_code == "A"
    assert sec.department == "Computer Science"
    assert sec.semester == 1
    assert sec.enrolled_count == 1


async def test_allocate_splits_when_over_largest_room_capacity(
    async_session, seeded_term, cs_sem1_course,
):
    """
    With a tight room inventory, more students than fit in one room
    must spill into a second cohort.
    """
    inventory = [("SMALL", 2)]
    agent = _make_agent(inventory)

    for i in range(3):
        s = await _add_student(
            async_session, semester=1, department="Computer Science",
            email=f"split-{i}@aau.edu.et",
            full_name=f"Split Student {i}",
        )
        await _register(async_session, s, seeded_term)

    result = await agent.allocate_sections(
        async_session, seeded_term.id, "Computer Science",
    )

    sections = (
        await async_session.execute(
            select(Section).where(Section.term_id == seeded_term.id).order_by(
                Section.section_code.asc(),
            )
        )
    ).scalars().all()
    assert [s.section_code for s in sections] == ["A", "B"]
    assert sections[0].enrolled_count == 2
    assert sections[1].enrolled_count == 1
    assert len(result.sections_created) == 2


async def test_allocate_is_per_department_call(
    async_session, seeded_term, cs_sem1_course,
):
    """
    Two students in different departments → two separate allocator
    runs produce two separate sections. A single call only scopes to
    one department.
    """
    cs_a = await _add_student(
        async_session, semester=1, department="Computer Science",
        email="cs-a@aau.edu.et", full_name="CS Studenta",
    )
    se_a = await _add_student(
        async_session, semester=1, department="Software Engineering",
        email="se-a@aau.edu.et", full_name="SE Studenta",
    )
    await _register(async_session, cs_a, seeded_term)
    await _register(async_session, se_a, seeded_term)

    agent = _make_agent()
    # First call: CS only.
    cs_result = await agent.allocate_sections(
        async_session, seeded_term.id, "Computer Science",
    )
    assert len(cs_result.sections_created) == 1
    assert {sec["department"] for sec in cs_result.sections_created} == {
        "Computer Science",
    }
    # Sibling department's student isn't touched
    se_reg = (
        await async_session.execute(
            select(Registration).where(Registration.student_id == se_a.id)
        )
    ).scalar_one()
    assert se_reg.section_id is None

    # Second call: SE.
    se_result = await agent.allocate_sections(
        async_session, seeded_term.id, "Software Engineering",
    )
    assert {sec["department"] for sec in se_result.sections_created} == {
        "Software Engineering",
    }


async def test_allocate_groups_by_semester_within_department(
    async_session, seeded_term, cs_sem1_course,
):
    """Two CS students at different semester levels → two cohorts."""
    s1 = await _add_student(
        async_session, semester=1, department="Computer Science",
        email="cs-s1@aau.edu.et", full_name="Csone Student",
    )
    s3 = await _add_student(
        async_session, semester=3, department="Computer Science",
        email="cs-s3@aau.edu.et", full_name="Csthree Student",
    )
    await _register(async_session, s1, seeded_term)
    await _register(async_session, s3, seeded_term)

    agent = _make_agent()
    await agent.allocate_sections(
        async_session, seeded_term.id, "Computer Science",
    )

    sections = (
        await async_session.execute(
            select(Section).where(
                Section.term_id == seeded_term.id,
                Section.department == "Computer Science",
            ).order_by(Section.semester.asc())
        )
    ).scalars().all()
    assert [(s.semester, s.section_code) for s in sections] == [
        (1, "A"), (3, "B"),
    ]


async def test_allocate_is_idempotent(
    async_session, seeded_term, cs_sem1_course,
):
    """Re-running with no new students leaves everything as-is."""
    s = await _add_student(
        async_session, semester=1, department="Computer Science",
        email="idem@aau.edu.et", full_name="Idem Test",
    )
    await _register(async_session, s, seeded_term)

    agent = _make_agent()
    first = await agent.allocate_sections(
        async_session, seeded_term.id, "Computer Science",
    )
    second = await agent.allocate_sections(
        async_session, seeded_term.id, "Computer Science",
    )

    assert len(first.sections_created) == 1
    assert len(second.sections_created) == 0
    assert len(second.students_placed) == 0


async def test_allocate_skips_students_in_other_departments(
    async_session, seeded_term, cs_sem1_course,
):
    """A run for CS must not place SE students into a CS cohort."""
    se = await _add_student(
        async_session, semester=1, department="Software Engineering",
        email="other-dept@aau.edu.et", full_name="Other Dept",
    )
    await _register(async_session, se, seeded_term)

    agent = _make_agent()
    result = await agent.allocate_sections(
        async_session, seeded_term.id, "Computer Science",
    )
    assert result.sections_created == []
    assert result.students_placed == []


# ── generate_schedule ────────────────────────────────────────────


async def test_generate_schedule_emits_credit_hours_worth_of_slots(
    async_session, seeded_term, cs_sem1_course,
):
    s = await _add_student(
        async_session, semester=1, department="Computer Science",
        email="hours@aau.edu.et", full_name="Hours Test",
    )
    await _register(async_session, s, seeded_term)

    agent = _make_agent()
    await agent.allocate_sections(async_session, seeded_term.id, "Computer Science")
    artefact = await agent.generate_schedule(
        async_session, seeded_term.id, "Computer Science",
    )

    slots = (
        await async_session.execute(select(ClassScheduleSlot))
    ).scalars().all()
    assert len(slots) == cs_sem1_course.credit_hours
    for slot in slots:
        assert slot.day_of_week in {"MON", "TUE", "WED", "THU", "FRI"}
        s_h, s_m = slot.start_time.hour, slot.start_time.minute
        e_h, e_m = slot.end_time.hour, slot.end_time.minute
        assert (e_h - s_h) * 60 + (e_m - s_m) == 60
    assert artefact.slots_created == cs_sem1_course.credit_hours


async def test_generate_schedule_pins_instructor_from_assignment(
    async_session, seeded_term, seeded_instructor, cs_sem1_course,
):
    async_session.add(InstructorAssignment(
        instructor_id=seeded_instructor.id,
        course_id=cs_sem1_course.id,
        term_id=seeded_term.id,
    ))
    s = await _add_student(
        async_session, semester=1, department="Computer Science",
        email="instr@aau.edu.et", full_name="Instr Test",
    )
    await _register(async_session, s, seeded_term)

    agent = _make_agent()
    await agent.allocate_sections(async_session, seeded_term.id, "Computer Science")
    await agent.generate_schedule(
        async_session, seeded_term.id, "Computer Science",
    )

    slots = (
        await async_session.execute(select(ClassScheduleSlot))
    ).scalars().all()
    assert slots, "expected at least one slot"
    assert all(slot.instructor_id == seeded_instructor.id for slot in slots)


async def test_process_task_runs_both_phases(
    async_session, seeded_term, cs_sem1_course,
):
    s = await _add_student(
        async_session, semester=1, department="Computer Science",
        email="full@aau.edu.et", full_name="Full Pipeline",
    )
    await _register(async_session, s, seeded_term)

    agent = _make_agent()
    payload = await agent.process_task({
        "session": async_session,
        "term_id": seeded_term.id,
        "department": "Computer Science",
    })

    assert payload["allocation"]["students_placed_count"] == 1
    assert payload["allocation"]["department"] == "Computer Science"
    assert payload["schedule"]["slots_created"] == cs_sem1_course.credit_hours
    assert payload["schedule"]["section_count"] == 1


# ── Isolation: scheduling one dept doesn't touch another ────────


async def test_scheduling_one_department_does_not_touch_another(
    async_session, seeded_term, cs_sem1_course,
):
    """
    Two students in different departments, same semester. Scheduling
    "Computer Science" must place the CS student and emit slots only
    for the CS cohort — the SE student stays unallocated and no SE
    section or slot is created.
    """
    cs_a = await _add_student(
        async_session, semester=1, department="Computer Science",
        email="iso-cs@aau.edu.et", full_name="Iso CS",
    )
    se_a = await _add_student(
        async_session, semester=1, department="Software Engineering",
        email="iso-se@aau.edu.et", full_name="Iso SE",
    )
    await _register(async_session, cs_a, seeded_term)
    await _register(async_session, se_a, seeded_term)

    agent = _make_agent()
    await agent.process_task({
        "session": async_session,
        "term_id": seeded_term.id,
        "department": "Computer Science",
    })

    # CS got a section
    cs_sections = (
        await async_session.execute(
            select(Section).where(
                Section.term_id == seeded_term.id,
                Section.department == "Computer Science",
            )
        )
    ).scalars().all()
    assert len(cs_sections) == 1
    cs_reg = (
        await async_session.execute(
            select(Registration).where(Registration.student_id == cs_a.id)
        )
    ).scalar_one()
    assert cs_reg.section_id == cs_sections[0].id

    # SE didn't
    se_sections = (
        await async_session.execute(
            select(Section).where(
                Section.term_id == seeded_term.id,
                Section.department == "Software Engineering",
            )
        )
    ).scalars().all()
    assert se_sections == []
    se_reg = (
        await async_session.execute(
            select(Registration).where(Registration.student_id == se_a.id)
        )
    ).scalar_one()
    assert se_reg.section_id is None

    # No slots emitted for any SE section
    se_slots = (
        await async_session.execute(
            select(ClassScheduleSlot).join(
                Section, Section.id == ClassScheduleSlot.section_id,
            ).where(Section.department == "Software Engineering")
        )
    ).scalars().all()
    assert se_slots == []


async def test_allocator_with_no_classrooms_for_department_reports_failure(
    async_session, seeded_term, cs_sem1_course,
):
    """
    If the department has no Classroom rows seeded (and no test
    override), the allocator records every affected student as
    failed instead of crashing.
    """
    s = await _add_student(
        async_session, semester=1, department="Computer Science",
        email="empty@aau.edu.et", full_name="Empty Dept",
    )
    await _register(async_session, s, seeded_term)

    # Force the agent to consult the DB (which has no CS rooms in
    # this in-memory test DB).
    agent = AcademicSchedulingAgent(room_inventory=None)
    result = await agent.allocate_sections(
        async_session, seeded_term.id, "Computer Science",
    )

    assert result.sections_created == []
    assert result.failed
    assert "no classrooms" in result.failed[0]["reason"].lower()
