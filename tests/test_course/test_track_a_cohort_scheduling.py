"""
Track A — cohort-based AcademicSchedulingAgent.

Replaces the old per-CourseOffering scheduling tests. Verifies the
new contract:

  - allocate_sections groups REGISTERED students by (department,
    current_semester) and pins each to a Section with a globally-
    unique code (A, B, C, …) and a room from the inventory.
  - generate_schedule lays out ClassScheduleSlot rows whose total
    weekly hours per course equal ``course.credit_hours``.
  - Re-runs are idempotent: students already pinned stay put,
    extra capacity is filled before new sections are created.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import time

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


# ── Tiny per-test fixture set ────────────────────────────────────


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

    agent = AcademicSchedulingAgent()
    result = await agent.allocate_sections(async_session, seeded_term.id)

    assert len(result.sections_created) == 1
    assert len(result.students_placed) == 1
    sec = (
        await async_session.execute(
            select(Section).where(Section.term_id == seeded_term.id)
        )
    ).scalar_one()
    assert sec.section_code == "A"   # global counter starts at A
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
    inventory = [("SMALL", 2)]      # one tiny room, capacity 2
    agent = AcademicSchedulingAgent(room_inventory=inventory)

    for i in range(3):
        s = await _add_student(
            async_session, semester=1, department="Computer Science",
            email=f"split-{i}@aau.edu.et",
            full_name=f"Split Student {i}",
        )
        await _register(async_session, s, seeded_term)

    result = await agent.allocate_sections(async_session, seeded_term.id)

    sections = (
        await async_session.execute(
            select(Section).where(Section.term_id == seeded_term.id).order_by(
                Section.section_code.asc(),
            )
        )
    ).scalars().all()
    assert [s.section_code for s in sections] == ["A", "B"]
    # Cohort A is full (2/2), cohort B holds the spill (1/2)
    assert sections[0].enrolled_count == 2
    assert sections[1].enrolled_count == 1


async def test_allocate_groups_by_department_and_semester(
    async_session, seeded_term, cs_sem1_course,
):
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

    agent = AcademicSchedulingAgent()
    await agent.allocate_sections(async_session, seeded_term.id)

    sections = (
        await async_session.execute(
            select(Section).where(Section.term_id == seeded_term.id).order_by(
                Section.department.asc(),
            )
        )
    ).scalars().all()
    # One cohort per department, both at semester 1.
    by_dept = {s.department: s for s in sections}
    assert set(by_dept) == {"Computer Science", "Software Engineering"}
    assert all(s.semester == 1 for s in sections)


async def test_allocate_is_idempotent(
    async_session, seeded_term, cs_sem1_course,
):
    """Re-running with no new students leaves everything as-is."""
    s = await _add_student(
        async_session, semester=1, department="Computer Science",
        email="idem@aau.edu.et", full_name="Idem Test",
    )
    await _register(async_session, s, seeded_term)

    agent = AcademicSchedulingAgent()
    first = await agent.allocate_sections(async_session, seeded_term.id)
    second = await agent.allocate_sections(async_session, seeded_term.id)

    assert len(first.sections_created) == 1
    assert len(second.sections_created) == 0
    assert len(second.students_placed) == 0


async def test_allocate_skips_students_with_no_department(
    async_session, seeded_term, cs_sem1_course,
):
    """A Student missing a department is recorded as failed, not crashed."""
    user = User(
        id=uuid.uuid4(), email="noemploy@aau.edu.et",
        first_name="No", last_name="Dept",
        hashed_password="x", role=UserRole.STUDENT, is_active=True,
    )
    async_session.add(user)
    await async_session.flush()
    student = Student(
        user_id=user.id, student_id="UGR/9990/14",
        full_name="No Dept", current_semester=1,
        department=None,
        enrollment_status=EnrollmentStatus.ACTIVE,
    )
    async_session.add(student)
    await async_session.flush()
    await _register(async_session, student, seeded_term)

    agent = AcademicSchedulingAgent()
    result = await agent.allocate_sections(async_session, seeded_term.id)

    assert result.failed
    assert result.failed[0]["reason"] == "student has no department recorded"


# ── generate_schedule ────────────────────────────────────────────


async def test_generate_schedule_emits_credit_hours_worth_of_slots(
    async_session, seeded_term, cs_sem1_course,
):
    """A 3-credit course should produce exactly 3 hour-blocks per section."""
    s = await _add_student(
        async_session, semester=1, department="Computer Science",
        email="hours@aau.edu.et", full_name="Hours Test",
    )
    await _register(async_session, s, seeded_term)

    agent = AcademicSchedulingAgent()
    await agent.allocate_sections(async_session, seeded_term.id)
    artefact = await agent.generate_schedule(async_session, seeded_term.id)

    slots = (
        await async_session.execute(
            select(ClassScheduleSlot)
        )
    ).scalars().all()
    assert len(slots) == cs_sem1_course.credit_hours
    # Each slot is exactly one hour (start + 1 == end)
    for slot in slots:
        assert slot.day_of_week in {"MON", "TUE", "WED", "THU", "FRI"}
        s_h, s_m = slot.start_time.hour, slot.start_time.minute
        e_h, e_m = slot.end_time.hour, slot.end_time.minute
        assert (e_h - s_h) * 60 + (e_m - s_m) == 60
    assert artefact.slots_created == cs_sem1_course.credit_hours


async def test_generate_schedule_pins_instructor_from_assignment(
    async_session, seeded_term, seeded_instructor, cs_sem1_course,
):
    """ClassScheduleSlot.instructor_id mirrors InstructorAssignment."""
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

    agent = AcademicSchedulingAgent()
    await agent.allocate_sections(async_session, seeded_term.id)
    await agent.generate_schedule(async_session, seeded_term.id)

    slots = (
        await async_session.execute(select(ClassScheduleSlot))
    ).scalars().all()
    assert all(slot.instructor_id == seeded_instructor.id for slot in slots)


async def test_process_task_runs_both_phases(
    async_session, seeded_term, cs_sem1_course,
):
    s = await _add_student(
        async_session, semester=1, department="Computer Science",
        email="full@aau.edu.et", full_name="Full Pipeline",
    )
    await _register(async_session, s, seeded_term)

    agent = AcademicSchedulingAgent()
    payload = await agent.process_task({
        "session": async_session,
        "term_id": seeded_term.id,
    })

    assert payload["allocation"]["students_placed_count"] == 1
    assert payload["schedule"]["slots_created"] == cs_sem1_course.credit_hours
    assert payload["schedule"]["section_count"] == 1
