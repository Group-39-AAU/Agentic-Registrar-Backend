"""
Track B PR 1 — effective-roster derivation tests.

Drives the pure ``derive_section_course_roster`` function and the
``InstructorGradingService`` wrapper through every edge case that
matters for the manually-tested seed scenario:

  * Original cohort members appear with ``is_added_via_drop=False``.
  * Students who joined via an approved add/drop batch appear with
    ``is_added_via_drop=True`` and the additional flag is the only
    difference in the row shape.
  * Students whose ``RegistrationCourse.is_dropped == True`` are
    absent (they cannot be graded — they don't take the course).
  * Students whose ``Registration.status`` is not in the attending
    set (e.g. ``REGISTRATION_OPEN`` draft) are absent.
  * A student who moves a course from B to A via add/drop appears
    on A's roster (tagged ADDED) and is absent from B's roster.
  * The ownership gate denies an instructor who does not own a slot
    for the (section, course) pair.
"""
from __future__ import annotations

import uuid
from datetime import date, time

import pytest
import pytest_asyncio

from app.modules.auth.models import User
from app.modules.course.exceptions import (
    EntityNotFoundError, UnauthorizedActorError,
)
from app.modules.course.grading.roster import (
    derive_section_course_roster,
)
from app.modules.course.grading.service import InstructorGradingService
from app.modules.course.models import (
    AcademicTerm, ClassScheduleSlot, Course, Instructor,
    Registration, RegistrationCourse, Section, Student,
    StudentScheduleAddition,
)
from app.shared.enums import (
    AcademicPhase, EnrollmentStatus, RegistrationStatus,
    SponsorshipType, UserRole,
)


# ── Fixtures ────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def grading_world(async_session):
    """
    A complete, deterministic scenario:

      - 1 term (open, phase ONE)
      - 1 course CS101 (3 credits, sem 1, CS dept)
      - 1 instructor (Dr. Lemma) — bound to BOTH sections' CS101
      - 1 other instructor (Dr. Other) — bound to nothing
      - 2 sections (A and B) under (term, CS, sem 1)
      - ClassScheduleSlot rows so each section has CS101 with Lemma
      - Several students in mixed states (registered / dropped / draft)
        plus one student who moves CS101 from B to A.

    Returns a dict of the entities so each test can grab what it needs.
    """
    term = AcademicTerm(
        term_name="Test-2026",
        phase=AcademicPhase.ONE,
        start_date=date(2026, 9, 1),
        end_date=date(2027, 1, 31),
        is_open=True,
    )
    course = Course(
        code="CS101", title="Intro Programming",
        credit_hours=3, semester=1, department="Computer Science",
    )
    async_session.add_all([term, course])
    await async_session.flush()

    # Instructor (Dr. Lemma) with backing User row.
    instr_user = User(
        id=uuid.uuid4(),
        email="lemma@aau.edu.et",
        first_name="Lemma", last_name="Bekele",
        hashed_password="x", role=UserRole.INSTRUCTOR, is_active=True,
    )
    other_instr_user = User(
        id=uuid.uuid4(),
        email="other@aau.edu.et",
        first_name="Other", last_name="Teacher",
        hashed_password="x", role=UserRole.INSTRUCTOR, is_active=True,
    )
    async_session.add_all([instr_user, other_instr_user])
    await async_session.flush()

    instructor = Instructor(
        user_id=instr_user.id,
        instructor_id="STAFF/T001/15",
        department="Computer Science",
    )
    other_instructor = Instructor(
        user_id=other_instr_user.id,
        instructor_id="STAFF/T002/15",
        department="Computer Science",
    )
    async_session.add_all([instructor, other_instructor])
    await async_session.flush()

    section_a = Section(
        term_id=term.id, department="Computer Science",
        semester=1, section_code="A", capacity=30, enrolled_count=0,
    )
    section_b = Section(
        term_id=term.id, department="Computer Science",
        semester=1, section_code="B", capacity=30, enrolled_count=0,
    )
    async_session.add_all([section_a, section_b])
    await async_session.flush()

    slot_a = ClassScheduleSlot(
        section_id=section_a.id, course_id=course.id,
        instructor_id=instructor.id,
        day_of_week="MON", start_time=time(9, 0), end_time=time(10, 0),
        room="LAB-1",
    )
    slot_b = ClassScheduleSlot(
        section_id=section_b.id, course_id=course.id,
        instructor_id=instructor.id,
        day_of_week="TUE", start_time=time(11, 0), end_time=time(12, 0),
        room="LAB-2",
    )
    async_session.add_all([slot_a, slot_b])
    await async_session.flush()

    return {
        "term": term,
        "course": course,
        "instructor": instructor,
        "instr_user": instr_user,
        "other_instructor": other_instructor,
        "other_instr_user": other_instr_user,
        "section_a": section_a,
        "section_b": section_b,
        "slot_a": slot_a,
        "slot_b": slot_b,
    }


async def _make_student(
    session, *, student_id: str, full_name: str,
) -> Student:
    """Helper: a student + its backing User row."""
    user = User(
        id=uuid.uuid4(),
        email=f"{student_id.lower().replace('/', '-')}@aau.edu.et",
        first_name=full_name.split()[0], last_name=full_name.split()[-1],
        hashed_password="x", role=UserRole.STUDENT, is_active=True,
    )
    session.add(user)
    await session.flush()
    s = Student(
        user_id=user.id,
        student_id=student_id,
        full_name=full_name,
        current_semester=1,
        department="Computer Science",
        sponsorship_type=SponsorshipType.GOVERNMENT,
        enrollment_status=EnrollmentStatus.ACTIVE,
    )
    session.add(s)
    await session.flush()
    return s


async def _register_in_section(
    session, *, student: Student, term: AcademicTerm,
    section: Section, course: Course,
    status_: RegistrationStatus = RegistrationStatus.REGISTERED,
    is_dropped: bool = False,
) -> Registration:
    reg = Registration(
        student_id=student.id, term_id=term.id, section_id=section.id,
        status=status_, sponsorship_type=SponsorshipType.GOVERNMENT,
    )
    session.add(reg)
    await session.flush()
    rc = RegistrationCourse(
        registration_id=reg.id, course_id=course.id, is_dropped=is_dropped,
    )
    session.add(rc)
    await session.flush()
    return reg


# ── derive_section_course_roster (pure function) ───────────────


async def test_originals_only_no_drops_no_adds(async_session, grading_world):
    """Baseline: three originals all appear, none flagged."""
    w = grading_world
    for sid, name in [
        ("UGR/0001/15", "Abel Tesfaye"),
        ("UGR/0002/15", "Bethel Demissie"),
        ("UGR/0003/15", "Chala Worku"),
    ]:
        student = await _make_student(async_session, student_id=sid, full_name=name)
        await _register_in_section(
            async_session, student=student, term=w["term"],
            section=w["section_a"], course=w["course"],
        )

    roster = await derive_section_course_roster(
        async_session, section_id=w["section_a"].id, course_id=w["course"].id,
    )
    assert [r.student_number for r in roster] == [
        "UGR/0001/15", "UGR/0002/15", "UGR/0003/15",
    ]
    assert all(r.is_added_via_drop is False for r in roster)


async def test_dropped_student_excluded(async_session, grading_world):
    """A student who dropped the course is absent from the roster."""
    w = grading_world
    keep = await _make_student(async_session, student_id="UGR/0001/15", full_name="Keep Me")
    await _register_in_section(
        async_session, student=keep, term=w["term"],
        section=w["section_a"], course=w["course"],
    )
    dropped = await _make_student(async_session, student_id="UGR/0002/15", full_name="Dropped Out")
    await _register_in_section(
        async_session, student=dropped, term=w["term"],
        section=w["section_a"], course=w["course"], is_dropped=True,
    )

    roster = await derive_section_course_roster(
        async_session, section_id=w["section_a"].id, course_id=w["course"].id,
    )
    student_numbers = {r.student_number for r in roster}
    assert "UGR/0001/15" in student_numbers
    assert "UGR/0002/15" not in student_numbers


async def test_draft_registration_excluded(async_session, grading_world):
    """A student whose registration is still REGISTRATION_OPEN is absent."""
    w = grading_world
    real = await _make_student(async_session, student_id="UGR/0001/15", full_name="Real Student")
    await _register_in_section(
        async_session, student=real, term=w["term"],
        section=w["section_a"], course=w["course"],
    )
    draft = await _make_student(async_session, student_id="UGR/0002/15", full_name="Draft Student")
    await _register_in_section(
        async_session, student=draft, term=w["term"],
        section=w["section_a"], course=w["course"],
        status_=RegistrationStatus.REGISTRATION_OPEN,
    )

    roster = await derive_section_course_roster(
        async_session, section_id=w["section_a"].id, course_id=w["course"].id,
    )
    student_numbers = {r.student_number for r in roster}
    assert student_numbers == {"UGR/0001/15"}


async def test_add_drop_window_status_is_attending(async_session, grading_world):
    """Students in the ADD_DROP_WINDOW status still attend — they appear."""
    w = grading_world
    s = await _make_student(async_session, student_id="UGR/0007/15", full_name="In Window")
    await _register_in_section(
        async_session, student=s, term=w["term"],
        section=w["section_a"], course=w["course"],
        status_=RegistrationStatus.ADD_DROP_WINDOW,
    )
    roster = await derive_section_course_roster(
        async_session, section_id=w["section_a"].id, course_id=w["course"].id,
    )
    assert [r.student_number for r in roster] == ["UGR/0007/15"]


async def test_added_via_schedule_addition_appears(async_session, grading_world):
    """A student whose StudentScheduleAddition points to (A, course) appears on A's roster."""
    w = grading_world
    # Cohort-B student moves CS101 to A via add/drop.
    mover = await _make_student(
        async_session, student_id="UGR/0050/15", full_name="Mover Student",
    )
    reg = await _register_in_section(
        async_session, student=mover, term=w["term"],
        section=w["section_b"], course=w["course"],
        is_dropped=True,  # CS101 in cohort B got dropped as part of the move
    )
    addition = StudentScheduleAddition(
        registration_id=reg.id,
        schedule_slot_id=w["slot_a"].id,
        course_id=w["course"].id,
        source_section_id=w["section_a"].id,
    )
    async_session.add(addition)
    await async_session.flush()

    roster_a = await derive_section_course_roster(
        async_session, section_id=w["section_a"].id, course_id=w["course"].id,
    )
    assert len(roster_a) == 1
    assert roster_a[0].student_number == "UGR/0050/15"
    assert roster_a[0].is_added_via_drop is True

    # And the mover is NOT on B's roster (their B RegCourse is dropped).
    roster_b = await derive_section_course_roster(
        async_session, section_id=w["section_b"].id, course_id=w["course"].id,
    )
    assert all(r.student_number != "UGR/0050/15" for r in roster_b)


async def test_added_student_dedup_across_multiple_slots(
    async_session, grading_world,
):
    """
    A 3-credit course generates one StudentScheduleAddition row per
    slot of the target section. The roster should still list the
    student exactly once.
    """
    w = grading_world
    extra_slot = ClassScheduleSlot(
        section_id=w["section_a"].id, course_id=w["course"].id,
        instructor_id=w["instructor"].id,
        day_of_week="WED", start_time=time(9, 0), end_time=time(10, 0),
        room="LAB-1",
    )
    async_session.add(extra_slot)
    await async_session.flush()

    mover = await _make_student(
        async_session, student_id="UGR/0051/15", full_name="Dupe Free",
    )
    reg = await _register_in_section(
        async_session, student=mover, term=w["term"],
        section=w["section_b"], course=w["course"], is_dropped=True,
    )
    async_session.add_all([
        StudentScheduleAddition(
            registration_id=reg.id,
            schedule_slot_id=w["slot_a"].id,
            course_id=w["course"].id,
            source_section_id=w["section_a"].id,
        ),
        StudentScheduleAddition(
            registration_id=reg.id,
            schedule_slot_id=extra_slot.id,
            course_id=w["course"].id,
            source_section_id=w["section_a"].id,
        ),
    ])
    await async_session.flush()

    roster = await derive_section_course_roster(
        async_session, section_id=w["section_a"].id, course_id=w["course"].id,
    )
    assert [r.student_number for r in roster] == ["UGR/0051/15"]


async def test_sorting_originals_first_then_alphabetical(
    async_session, grading_world,
):
    """Originals come first; within each group, sorted by student_number."""
    w = grading_world
    # Originals (intentionally inserted out of order)
    for sid, name in [
        ("UGR/0030/15", "C Last"),
        ("UGR/0010/15", "A First"),
        ("UGR/0020/15", "B Middle"),
    ]:
        student = await _make_student(async_session, student_id=sid, full_name=name)
        await _register_in_section(
            async_session, student=student, term=w["term"],
            section=w["section_a"], course=w["course"],
        )

    # Added (also out of order)
    for sid, name in [("UGR/0080/15", "Z Add"), ("UGR/0070/15", "Y Add")]:
        s = await _make_student(async_session, student_id=sid, full_name=name)
        reg = await _register_in_section(
            async_session, student=s, term=w["term"],
            section=w["section_b"], course=w["course"], is_dropped=True,
        )
        async_session.add(StudentScheduleAddition(
            registration_id=reg.id,
            schedule_slot_id=w["slot_a"].id,
            course_id=w["course"].id,
            source_section_id=w["section_a"].id,
        ))
    await async_session.flush()

    roster = await derive_section_course_roster(
        async_session, section_id=w["section_a"].id, course_id=w["course"].id,
    )
    assert [r.student_number for r in roster] == [
        "UGR/0010/15", "UGR/0020/15", "UGR/0030/15",  # originals
        "UGR/0070/15", "UGR/0080/15",                  # added
    ]


# ── InstructorGradingService — auth and pair listing ────────────


async def test_list_my_section_assignments_returns_pairs(
    async_session, grading_world,
):
    """
    The instructor teaches CS101 in both Section A (with 1 slot) and
    Section B (with 1 slot) — the service should return 2 entries.
    """
    w = grading_world
    svc = InstructorGradingService(async_session)
    entries = await svc.list_my_section_assignments(
        user_id=w["instr_user"].id, term_id=w["term"].id,
    )
    assert len(entries) == 2
    pairs = {(e.section_code, e.course_code, e.slot_count) for e in entries}
    assert pairs == {("A", "CS101", 1), ("B", "CS101", 1)}


async def test_list_my_sections_empty_for_other_instructor(
    async_session, grading_world,
):
    """Another instructor teaches nothing — empty list, not an error."""
    w = grading_world
    svc = InstructorGradingService(async_session)
    entries = await svc.list_my_section_assignments(
        user_id=w["other_instr_user"].id, term_id=w["term"].id,
    )
    assert entries == []


async def test_get_roster_denies_unauthorised_instructor(
    async_session, grading_world,
):
    """Calling instructor must own a slot for the (section, course) pair."""
    w = grading_world
    svc = InstructorGradingService(async_session)
    with pytest.raises(UnauthorizedActorError):
        await svc.get_section_course_roster(
            user_id=w["other_instr_user"].id,
            section_id=w["section_a"].id,
            course_id=w["course"].id,
        )


async def test_get_roster_404_for_unknown_section(
    async_session, grading_world,
):
    """Unknown section → EntityNotFoundError (router maps to 404)."""
    w = grading_world
    svc = InstructorGradingService(async_session)
    with pytest.raises(EntityNotFoundError):
        await svc.get_section_course_roster(
            user_id=w["instr_user"].id,
            section_id=uuid.uuid4(),
            course_id=w["course"].id,
        )


async def test_get_roster_no_instructor_profile_403(
    async_session, grading_world,
):
    """A user with no Instructor row gets a 403, not a 500."""
    w = grading_world
    stranger = User(
        id=uuid.uuid4(),
        email="stranger@aau.edu.et",
        first_name="Stranger", last_name="X",
        hashed_password="x", role=UserRole.INSTRUCTOR, is_active=True,
    )
    async_session.add(stranger)
    await async_session.flush()

    svc = InstructorGradingService(async_session)
    with pytest.raises(UnauthorizedActorError):
        await svc.get_section_course_roster(
            user_id=stranger.id,
            section_id=w["section_a"].id,
            course_id=w["course"].id,
        )


async def test_get_roster_summary_counts(async_session, grading_world):
    """The response carries total/original_count/added_count tallies."""
    w = grading_world
    # 2 originals
    for sid, name in [("UGR/0001/15", "Orig One"), ("UGR/0002/15", "Orig Two")]:
        s = await _make_student(async_session, student_id=sid, full_name=name)
        await _register_in_section(
            async_session, student=s, term=w["term"],
            section=w["section_a"], course=w["course"],
        )
    # 1 added from B
    mover = await _make_student(async_session, student_id="UGR/0099/15", full_name="The Mover")
    reg = await _register_in_section(
        async_session, student=mover, term=w["term"],
        section=w["section_b"], course=w["course"], is_dropped=True,
    )
    async_session.add(StudentScheduleAddition(
        registration_id=reg.id,
        schedule_slot_id=w["slot_a"].id,
        course_id=w["course"].id,
        source_section_id=w["section_a"].id,
    ))
    await async_session.flush()

    svc = InstructorGradingService(async_session)
    resp = await svc.get_section_course_roster(
        user_id=w["instr_user"].id,
        section_id=w["section_a"].id,
        course_id=w["course"].id,
    )
    assert resp.total == 3
    assert resp.original_count == 2
    assert resp.added_count == 1
    assert resp.section_code == "A"
    assert resp.course_code == "CS101"
