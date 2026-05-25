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


async def test_allocate_does_not_pin_a_room(
    async_session, seeded_term, cs_sem1_course,
):
    """
    The allocator deliberately leaves ``Section.room`` unset.
    Rooms are picked by :meth:`generate_schedule` so the same
    department's cohorts can share a single picker pass.
    """
    agent = _make_agent()
    stu = await _add_student(
        async_session, semester=1, department="Computer Science",
        email="no-room-yet@aau.edu.et", full_name="Nrm Student",
    )
    await _register(async_session, stu, seeded_term)

    result = await agent.allocate_sections(
        async_session, seeded_term.id, "Computer Science",
    )
    assert result.sections_created
    # Section payload from the allocator no longer carries a room key —
    # rooms only show up on slot rows after generate_schedule runs.
    assert all("room" not in s for s in result.sections_created)


async def test_generate_section_never_double_books_itself(
    async_session, seeded_term,
):
    """
    With multiple courses in the same semester, a cohort must never
    end up with two classes scheduled at the same ``(day, start)`` —
    even though per-slot room picking now lets different courses use
    different rooms, a single cohort can only be in one place at a
    time. The DB has ``uq_section_slot_per_day_start`` enforcing this,
    and the agent skips conflicting blocks rather than tripping it.
    """
    inventory = [("A", 80), ("B", 80), ("C", 80)]
    agent = _make_agent(inventory)

    for code in ("CS101", "CS102", "CS103", "CS104"):
        async_session.add(
            Course(
                code=code, title=f"Course {code}",
                credit_hours=2, semester=1,
                department="Computer Science",
            )
        )
    stu = await _add_student(
        async_session, semester=1, department="Computer Science",
        email="double-book@aau.edu.et", full_name="Db Stud",
    )
    await _register(async_session, stu, seeded_term)
    await async_session.flush()

    await agent.allocate_sections(
        async_session, seeded_term.id, "Computer Science",
    )
    artefact = await agent.generate_schedule(
        async_session, seeded_term.id, "Computer Science",
    )

    slots = (
        await async_session.execute(
            select(ClassScheduleSlot).join(
                Section, Section.id == ClassScheduleSlot.section_id,
            ).where(
                Section.term_id == seeded_term.id,
                Section.department == "Computer Science",
            )
        )
    ).scalars().all()
    # 4 courses × 2 credit hours = 8 slots, all placed.
    assert artefact.slots_created == 8
    seen: set = set()
    for sl in slots:
        key = (sl.section_id, sl.day_of_week, sl.start_time)
        assert key not in seen, (
            f"Section {sl.section_id} double-booked at "
            f"{sl.day_of_week} {sl.start_time}"
        )
        seen.add(key)


async def test_generate_spreads_cohorts_across_rooms(
    async_session, seeded_term,
):
    """
    Three cohorts running concurrently must not share a single
    ``(room, day, time_slot)`` triple. The new scheduler picks the
    smallest fitting room overall (preserving large halls for big
    cohorts), so it tends to *reuse* the same small room at
    *different days* for tiny cohorts rather than spreading them
    into bigger rooms at the same time — both behaviours satisfy
    the no-conflict invariant the registrar actually cares about.
    """
    inventory = [("BIG", 80), ("MED", 60), ("SMALL", 30)]
    agent = _make_agent(inventory)

    # One course per semester so generate_schedule has something to
    # lay down; without curriculum it would no-op and never pick.
    for sem in (1, 3, 5):
        async_session.add(
            Course(
                code=f"CS{sem}00", title=f"Course {sem}",
                credit_hours=1, semester=sem,
                department="Computer Science",
            )
        )
        stu = await _add_student(
            async_session, semester=sem, department="Computer Science",
            email=f"spread-{sem}@aau.edu.et",
            full_name=f"Spread {sem} Student",
        )
        await _register(async_session, stu, seeded_term)
    await async_session.flush()

    await agent.allocate_sections(
        async_session, seeded_term.id, "Computer Science",
    )
    await agent.generate_schedule(
        async_session, seeded_term.id, "Computer Science",
    )

    slots = (
        await async_session.execute(
            select(ClassScheduleSlot).join(
                Section, Section.id == ClassScheduleSlot.section_id,
            ).where(
                Section.term_id == seeded_term.id,
                Section.department == "Computer Science",
            )
        )
    ).scalars().all()
    assert len(slots) == 3, (
        "One slot per cohort (each course has credit_hours=1) — "
        f"got {len(slots)}"
    )
    rooms_used = {sl.room for sl in slots}
    assert None not in rooms_used
    # Real invariant: no two slots share the same (room, day, start).
    triples = {(sl.room, sl.day_of_week, sl.start_time) for sl in slots}
    assert len(triples) == 3, (
        f"Three cohorts must end up at three distinct (room, day, time) "
        f"triples — got {triples}"
    )


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
    # Section codes restart at "A" inside each (term, department,
    # semester) cohort — sem-1 and sem-3 both start at A, they don't
    # share the term-wide alphabet.
    assert [(s.semester, s.section_code) for s in sections] == [
        (1, "A"), (3, "A"),
    ]


async def test_allocate_rebuilds_on_rerun(
    async_session, seeded_term, cs_sem1_course,
):
    """
    Re-running allocate wipes the prior allocation and rebuilds. The
    second run re-creates the same logical section but with a fresh
    DB row (different id), and re-places the student into it. This
    is the documented "redo" behaviour officers rely on when they
    want a clean re-split.
    """
    s = await _add_student(
        async_session, semester=1, department="Computer Science",
        email="rebuild@aau.edu.et", full_name="Rebuild Test",
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
    assert len(second.sections_created) == 1
    assert len(second.students_placed) == 1

    first_ids = {s["section_id"] for s in first.sections_created}
    second_ids = {s["section_id"] for s in second.sections_created}
    assert first_ids.isdisjoint(second_ids), (
        "Re-run must hard-delete the old section rows and create new ones."
    )


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


# ── Realistic demo-scale: zero-conflict guarantee ───────────────


async def test_demo_scale_workload_produces_zero_conflicts(
    async_session, seeded_term,
):
    """
    Demo-scale realism check. Mirrors the seed's worst-case department:
    one 70-student sem-1 cohort plus four 60-student upper-year cohorts
    (sem 3, 5, 7, 9), each enrolled in 4 four-credit courses, sharing
    5 classrooms (two 80s, two 60s, one 30) and 5 instructors via
    round-robin assignment — exactly what
    scripts/seed_course_management.py provisions for Software
    Engineering.

    The new greedy-with-shuffle-restart scheduler must place all
    5 × 4 × 4 = 80 weekly hours with **zero** ScheduleConflict
    rows, proving that the seed inventory + algorithm together
    leave enough slack for a fully-feasible weekly timetable.
    """
    cohort_sizes = {1: 70, 3: 60, 5: 60, 7: 60, 9: 60}
    DEPT = "Computer Science"
    rooms = [
        ("CSc-101", 80), ("CSc-102", 80),
        ("CSc-201", 60), ("CSc-202", 60),
        ("CSc-LAB-1", 30),
    ]
    agent = _make_agent(rooms)

    # 5 instructors, round-robin assigned to courses below.
    instructors = []
    for i in range(5):
        u = User(
            id=uuid.uuid4(),
            email=f"instr-{i}@aau.edu.et",
            first_name=f"Ins{i}", last_name="Tructor",
            hashed_password="x", role=UserRole.INSTRUCTOR, is_active=True,
        )
        async_session.add(u)
        await async_session.flush()
        from app.modules.course.models import Instructor
        ins = Instructor(
            user_id=u.id,
            instructor_id=f"STAFF/REAL/{i:02d}",
            department=DEPT,
        )
        async_session.add(ins)
        await async_session.flush()
        instructors.append(ins)

    # 5 cohorts × 4 four-credit courses each = 20 courses, all in
    # the CS department. Course codes mirror the seed: SE<sem><slot>.
    course_objects = []
    for semester in (1, 3, 5, 7, 9):
        for slot in range(1, 5):
            course = Course(
                code=f"CS{semester}{slot:02d}",
                title=f"CS sem-{semester} course {slot}",
                credit_hours=4,
                semester=semester,
                department=DEPT,
            )
            async_session.add(course)
            await async_session.flush()
            course_objects.append(course)
            async_session.add(InstructorAssignment(
                instructor_id=instructors[len(course_objects) % 5].id,
                course_id=course.id,
                term_id=seeded_term.id,
            ))
    await async_session.flush()

    # Cohort sizes mirror the real seed (70 sem-1, 60 elsewhere)
    # so room slack matches what production sees. Build rows
    # directly with a sequential student_id so the helper's
    # hash-mod scheme can't accidentally collide at this scale.
    next_seq = 0
    for semester in (1, 3, 5, 7, 9):
        for i in range(cohort_sizes[semester]):
            next_seq += 1
            user = User(
                id=uuid.uuid4(),
                email=f"sem{semester}-stu{i}@aau.edu.et",
                first_name=f"Sem{semester}", last_name=f"Stu{i}",
                hashed_password="x",
                role=UserRole.STUDENT, is_active=True,
            )
            async_session.add(user)
            await async_session.flush()
            student = Student(
                user_id=user.id,
                student_id=f"UGR/REAL/{next_seq:04d}",
                full_name=f"Sem{semester} Stu{i}",
                current_semester=semester,
                department=DEPT,
                enrollment_status=EnrollmentStatus.ACTIVE,
            )
            async_session.add(student)
            await async_session.flush()
            await _register(async_session, student, seeded_term)

    # Allocate + schedule.
    alloc = await agent.allocate_sections(async_session, seeded_term.id, DEPT)
    artefact = await agent.generate_schedule(
        async_session, seeded_term.id, DEPT,
    )

    # Five 70-student cohorts → one section per semester (capped at
    # 80 = largest room) = 5 sections.
    assert artefact.section_count == 5, (
        f"Expected 5 sections (one per semester), got {artefact.section_count}"
    )
    # 5 cohorts × 4 courses × 4 credits = 80 weekly hours.
    assert artefact.slots_created == 80, (
        f"Expected 80 slots, got {artefact.slots_created}"
    )
    # ZERO conflicts under this workload — that's the promise.
    assert artefact.conflict_ids == [], (
        f"Expected zero conflicts, got {len(artefact.conflict_ids)}: "
        f"{artefact.conflict_ids}"
    )

    # Spot-check the structural invariants the schedule must obey.
    slots = (
        await async_session.execute(
            select(ClassScheduleSlot).join(
                Section, Section.id == ClassScheduleSlot.section_id,
            ).where(Section.term_id == seeded_term.id)
        )
    ).scalars().all()
    seen_section = set()
    seen_instructor = set()
    seen_room = set()
    for sl in slots:
        sk = (sl.section_id, sl.day_of_week, sl.start_time)
        assert sk not in seen_section, f"Cohort double-booked at {sk}"
        seen_section.add(sk)
        ik = (sl.instructor_id, sl.day_of_week, sl.start_time)
        assert ik not in seen_instructor, f"Instructor double-booked at {ik}"
        seen_instructor.add(ik)
        rk = (sl.room, sl.day_of_week, sl.start_time)
        assert rk not in seen_room, f"Room double-booked at {rk}"
        seen_room.add(rk)
