"""
Track B — PR 1 manual-test seed data.

Materialises a small, self-contained scenario so you can manually
verify the two roster endpoints introduced in PR 1:

    GET /api/v1/courses/grading/me/sections
    GET /api/v1/courses/grading/sections/{sid}/courses/{cid}/roster

The scenario builds a real cohort with an instructor teaching two
courses across two sections, plus the three add/drop edge cases the
roster derivation has to handle:

    - 6 students originally in Section A taking CS101
    - 5 students originally in Section B taking CS101
    - 1 student from Section B who ADDED CS101 from Section A
      (StudentScheduleAddition row → should appear on Section A's
       CS101 roster with is_added_via_drop=True)
    - 1 student from Section A who DROPPED CS101 entirely
      (RegistrationCourse.is_dropped=True → should NOT appear)
    - 1 student in Section A whose registration is still in
      REGISTRATION_OPEN (draft) — should NOT appear (not attending yet)

Manual-test expected outcome for Section A × CS101:
    total           = 5
    original_count  = 4  (Abel, Bethel, Chala, Dawit)
    added_count     = 1  (Kalkidan, is_added_via_drop=True)
    Eyerusalem is absent (dropped CS101).
    Feven is absent (registration still in REGISTRATION_OPEN draft).

For Section B × CS101:
    total           = 4
    original_count  = 4  (Genet, Hana, Ibrahim, Jemberu)
    added_count     = 0
    Kalkidan is absent — they moved CS101 to Section A, so their
    RegistrationCourse in B is is_dropped=True and they show up on
    A's roster instead.

The instructor (Dr. Lemma) teaches BOTH sections' CS101 slots, so the
``GET /me/sections`` endpoint returns two entries: (A, CS101) and
(B, CS101).

Idempotent: re-running this script reuses any rows already present.

Usage:
    cd /path/to/Agentic-Registrar-Backend
    source venv/bin/activate
    python scripts/seed_track_b_roster.py

Login credentials (instructor):
    email    : staff-9991-15@aau.edu.et
    password : password123
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, time, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.security import hash_password
from app.modules.auth.models import User
from app.modules.course.models import (
    AcademicTerm, ClassScheduleSlot, Course, Instructor,
    InstructorAssignment, Registration, RegistrationCourse,
    Section, Student, StudentScheduleAddition,
)
from app.shared.enums import (
    AcademicPhase, EnrollmentStatus, RegistrationStatus,
    SponsorshipType, UserRole,
)


DATABASE_URL = str(settings.DATABASE_URL)


# ── Stable UUID namespace so re-runs produce the same IDs ──

_NS = uuid.UUID("b0a3e000-0000-4000-8000-000000000001")  # "track b pr1"


def _uid(*parts: str) -> uuid.UUID:
    return uuid.uuid5(_NS, "/".join(parts))


# ── Scenario constants ──

TERM_NAME = "Track-B-Demo-2026"
DEPARTMENT = "Software Engineering"
SEMESTER = 1

COURSE_CODE = "CS101"
COURSE_TITLE = "Introduction to Programming"
COURSE_CREDITS = 3

SECONDARY_COURSE_CODE = "MATH101"
SECONDARY_COURSE_TITLE = "Calculus I"
SECONDARY_COURSE_CREDITS = 3

INSTRUCTOR_STAFF_ID = "STAFF/9991/15"
INSTRUCTOR_FIRST = "Lemma"
INSTRUCTOR_LAST = "Bekele"

# 6 originals in Section A (one will drop CS101, one is draft)
SECTION_A_STUDENTS = [
    ("UGR/9001/15", "Abel Tesfaye"),
    ("UGR/9002/15", "Bethel Demissie"),
    ("UGR/9003/15", "Chala Worku"),
    ("UGR/9004/15", "Dawit Asefa"),
    ("UGR/9005/15", "Eyerusalem Hailu"),   # will DROP CS101
    ("UGR/9006/15", "Feven Mulu"),         # will be in REGISTRATION_OPEN (draft)
]
DROPPER_INDEX = 4   # Eyerusalem
DRAFT_INDEX = 5     # Feven

# 5 originals in Section B (one will ADD CS101 from Section A)
SECTION_B_STUDENTS = [
    ("UGR/9011/15", "Genet Aklilu"),
    ("UGR/9012/15", "Hana Yonas"),
    ("UGR/9013/15", "Ibrahim Mohammed"),
    ("UGR/9014/15", "Jemberu Kassa"),
    ("UGR/9015/15", "Kalkidan Tadesse"),   # will ADD CS101 from Section A
]
MOVER_INDEX = 4   # Kalkidan moves CS101 from B to A


# ══════════════════════════════════════════════════════════════
#  Seed helpers (all idempotent)
# ══════════════════════════════════════════════════════════════


async def _ensure_user(
    session: AsyncSession,
    *,
    email: str,
    first_name: str,
    last_name: str,
    role: UserRole,
    user_uid: uuid.UUID,
) -> User:
    existing = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if existing:
        return existing
    user = User(
        id=user_uid,
        email=email,
        first_name=first_name,
        last_name=last_name,
        hashed_password=hash_password("password123"),
        role=role,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def _ensure_term(session: AsyncSession) -> AcademicTerm:
    existing = (
        await session.execute(
            select(AcademicTerm).where(
                AcademicTerm.term_name == TERM_NAME,
                AcademicTerm.phase == AcademicPhase.ONE,
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    term = AcademicTerm(
        id=_uid("term", TERM_NAME),
        term_name=TERM_NAME,
        phase=AcademicPhase.ONE,
        start_date=date(2026, 9, 1),
        end_date=date(2027, 1, 31),
        is_open=True,
        description="Track B PR 1 manual-test demo term.",
    )
    session.add(term)
    await session.flush()
    return term


async def _ensure_course(
    session: AsyncSession, *, code: str, title: str, credits: int,
) -> Course:
    existing = (
        await session.execute(select(Course).where(Course.code == code))
    ).scalar_one_or_none()
    if existing:
        return existing
    course = Course(
        id=_uid("course", code),
        code=code,
        title=title,
        credit_hours=credits,
        semester=SEMESTER,
        department=DEPARTMENT,
        description=f"{title} — Track B demo course.",
    )
    session.add(course)
    await session.flush()
    return course


async def _ensure_instructor(session: AsyncSession) -> Instructor:
    existing = (
        await session.execute(
            select(Instructor).where(
                Instructor.instructor_id == INSTRUCTOR_STAFF_ID
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    slug = INSTRUCTOR_STAFF_ID.lower().replace("/", "-")
    user = await _ensure_user(
        session,
        email=f"{slug}@aau.edu.et",
        first_name=INSTRUCTOR_FIRST,
        last_name=INSTRUCTOR_LAST,
        role=UserRole.INSTRUCTOR,
        user_uid=_uid("user", "instructor", INSTRUCTOR_STAFF_ID),
    )
    instructor = Instructor(
        id=_uid("instructor", INSTRUCTOR_STAFF_ID),
        user_id=user.id,
        instructor_id=INSTRUCTOR_STAFF_ID,
        department=DEPARTMENT,
    )
    session.add(instructor)
    await session.flush()
    return instructor


async def _ensure_instructor_assignment(
    session: AsyncSession,
    *,
    instructor: Instructor,
    course: Course,
    term: AcademicTerm,
) -> InstructorAssignment:
    existing = (
        await session.execute(
            select(InstructorAssignment).where(
                InstructorAssignment.instructor_id == instructor.id,
                InstructorAssignment.course_id == course.id,
                InstructorAssignment.term_id == term.id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    assignment = InstructorAssignment(
        id=_uid("assignment", str(instructor.id), str(course.id), str(term.id)),
        instructor_id=instructor.id,
        course_id=course.id,
        term_id=term.id,
    )
    session.add(assignment)
    await session.flush()
    return assignment


async def _ensure_section(
    session: AsyncSession,
    *,
    term: AcademicTerm,
    section_code: str,
    capacity: int = 30,
) -> Section:
    existing = (
        await session.execute(
            select(Section).where(
                Section.term_id == term.id,
                Section.department == DEPARTMENT,
                Section.semester == SEMESTER,
                Section.section_code == section_code,
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    section = Section(
        id=_uid("section", str(term.id), DEPARTMENT, str(SEMESTER), section_code),
        term_id=term.id,
        department=DEPARTMENT,
        semester=SEMESTER,
        section_code=section_code,
        capacity=capacity,
        enrolled_count=0,
    )
    session.add(section)
    await session.flush()
    return section


async def _ensure_slot(
    session: AsyncSession,
    *,
    section: Section,
    course: Course,
    instructor: Instructor,
    day_of_week: str,
    start_hour: int,
    end_hour: int,
    room: str,
) -> ClassScheduleSlot:
    existing = (
        await session.execute(
            select(ClassScheduleSlot).where(
                ClassScheduleSlot.section_id == section.id,
                ClassScheduleSlot.day_of_week == day_of_week,
                ClassScheduleSlot.start_time == time(start_hour, 0),
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    slot = ClassScheduleSlot(
        id=_uid(
            "slot",
            str(section.id), str(course.id),
            day_of_week, str(start_hour),
        ),
        section_id=section.id,
        course_id=course.id,
        instructor_id=instructor.id,
        day_of_week=day_of_week,
        start_time=time(start_hour, 0),
        end_time=time(end_hour, 0),
        room=room,
    )
    session.add(slot)
    await session.flush()
    return slot


async def _ensure_student(
    session: AsyncSession,
    *,
    student_id: str,
    full_name: str,
) -> Student:
    existing = (
        await session.execute(
            select(Student).where(Student.student_id == student_id)
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    slug = student_id.lower().replace("/", "-")
    first, *rest = full_name.split(" ", 1)
    last = rest[0] if rest else "."
    user = await _ensure_user(
        session,
        email=f"{slug}@aau.edu.et",
        first_name=first,
        last_name=last,
        role=UserRole.STUDENT,
        user_uid=_uid("user", "student", student_id),
    )
    student = Student(
        id=_uid("student", student_id),
        user_id=user.id,
        student_id=student_id,
        full_name=full_name,
        current_semester=SEMESTER,
        department=DEPARTMENT,
        sponsorship_type=SponsorshipType.GOVERNMENT,
        enrollment_status=EnrollmentStatus.ACTIVE,
    )
    session.add(student)
    await session.flush()
    return student


async def _ensure_registration(
    session: AsyncSession,
    *,
    student: Student,
    term: AcademicTerm,
    section: Section,
    status_: RegistrationStatus,
) -> Registration:
    existing = (
        await session.execute(
            select(Registration).where(
                Registration.student_id == student.id,
                Registration.term_id == term.id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        # Refresh status/section if needed (idempotent re-runs).
        if existing.section_id != section.id:
            existing.section_id = section.id
        if existing.status != status_:
            existing.status = status_
        await session.flush()
        return existing
    registration = Registration(
        id=_uid("registration", str(student.id), str(term.id)),
        student_id=student.id,
        term_id=term.id,
        section_id=section.id,
        status=status_,
        sponsorship_type=SponsorshipType.GOVERNMENT,
        finalised_at=datetime.now(timezone.utc)
        if status_ == RegistrationStatus.REGISTERED
        else None,
    )
    session.add(registration)
    await session.flush()
    return registration


async def _ensure_registration_course(
    session: AsyncSession,
    *,
    registration: Registration,
    course: Course,
    is_dropped: bool,
) -> RegistrationCourse:
    existing = (
        await session.execute(
            select(RegistrationCourse).where(
                RegistrationCourse.registration_id == registration.id,
                RegistrationCourse.course_id == course.id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        if existing.is_dropped != is_dropped:
            existing.is_dropped = is_dropped
            await session.flush()
        return existing
    rc = RegistrationCourse(
        id=_uid(
            "registration-course",
            str(registration.id), str(course.id),
        ),
        registration_id=registration.id,
        course_id=course.id,
        is_dropped=is_dropped,
    )
    session.add(rc)
    await session.flush()
    return rc


async def _ensure_schedule_addition(
    session: AsyncSession,
    *,
    registration: Registration,
    slot: ClassScheduleSlot,
    course: Course,
    source_section: Section,
) -> StudentScheduleAddition:
    existing = (
        await session.execute(
            select(StudentScheduleAddition).where(
                StudentScheduleAddition.registration_id == registration.id,
                StudentScheduleAddition.schedule_slot_id == slot.id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    addition = StudentScheduleAddition(
        id=_uid(
            "schedule-addition",
            str(registration.id), str(slot.id),
        ),
        registration_id=registration.id,
        schedule_slot_id=slot.id,
        course_id=course.id,
        source_section_id=source_section.id,
    )
    session.add(addition)
    await session.flush()
    return addition


# ══════════════════════════════════════════════════════════════
#  Orchestrator
# ══════════════════════════════════════════════════════════════


async def seed(session: AsyncSession) -> None:
    print(">> Track B PR 1 — roster seed")

    term = await _ensure_term(session)
    print(f"   term:          {term.term_name} ({term.id})")

    course = await _ensure_course(
        session, code=COURSE_CODE,
        title=COURSE_TITLE, credits=COURSE_CREDITS,
    )
    secondary_course = await _ensure_course(
        session, code=SECONDARY_COURSE_CODE,
        title=SECONDARY_COURSE_TITLE, credits=SECONDARY_COURSE_CREDITS,
    )
    print(f"   primary course:{course.code} ({course.id})")
    print(f"   second course: {secondary_course.code} ({secondary_course.id})")

    instructor = await _ensure_instructor(session)
    print(f"   instructor:    {INSTRUCTOR_FIRST} {INSTRUCTOR_LAST} ({instructor.id})")
    print(f"                  login: staff-9991-15@aau.edu.et / password123")

    await _ensure_instructor_assignment(
        session, instructor=instructor, course=course, term=term,
    )

    section_a = await _ensure_section(
        session, term=term, section_code="A", capacity=30,
    )
    section_b = await _ensure_section(
        session, term=term, section_code="B", capacity=30,
    )
    print(f"   section A:     {section_a.id}")
    print(f"   section B:     {section_b.id}")

    # Slots: instructor teaches CS101 in both A and B.
    slot_a_cs101 = await _ensure_slot(
        session,
        section=section_a, course=course, instructor=instructor,
        day_of_week="MON", start_hour=9, end_hour=10, room="LAB-1",
    )
    await _ensure_slot(
        session,
        section=section_a, course=course, instructor=instructor,
        day_of_week="WED", start_hour=9, end_hour=10, room="LAB-1",
    )
    await _ensure_slot(
        session,
        section=section_a, course=course, instructor=instructor,
        day_of_week="FRI", start_hour=9, end_hour=10, room="LAB-1",
    )
    await _ensure_slot(
        session,
        section=section_b, course=course, instructor=instructor,
        day_of_week="TUE", start_hour=11, end_hour=12, room="LAB-2",
    )
    await _ensure_slot(
        session,
        section=section_b, course=course, instructor=instructor,
        day_of_week="THU", start_hour=11, end_hour=12, room="LAB-2",
    )
    # Section A also offers a MATH101 slot taught by the SAME
    # instructor — verifies that GET /me/sections returns two
    # (section, course) pairs for Section A, not collapses them.
    await _ensure_slot(
        session,
        section=section_a, course=secondary_course, instructor=instructor,
        day_of_week="TUE", start_hour=14, end_hour=15, room="LAB-1",
    )
    print(f"   slots:         instructor teaches CS101 in A (3 slots) "
          f"+ B (2 slots) + MATH101 in A (1 slot)")

    # ── Section A students ──
    print(f"\n>> Section A students")
    for i, (student_id, full_name) in enumerate(SECTION_A_STUDENTS):
        student = await _ensure_student(
            session, student_id=student_id, full_name=full_name,
        )
        if i == DRAFT_INDEX:
            # Still in draft — should NOT appear on the roster.
            reg = await _ensure_registration(
                session, student=student, term=term, section=section_a,
                status_=RegistrationStatus.REGISTRATION_OPEN,
            )
            await _ensure_registration_course(
                session, registration=reg, course=course, is_dropped=False,
            )
            print(f"   - {student_id} {full_name:<24} (DRAFT — excluded)")
        else:
            reg = await _ensure_registration(
                session, student=student, term=term, section=section_a,
                status_=RegistrationStatus.REGISTERED,
            )
            dropped = (i == DROPPER_INDEX)
            await _ensure_registration_course(
                session, registration=reg, course=course, is_dropped=dropped,
            )
            tag = "DROPPED — excluded" if dropped else "ORIGINAL"
            print(f"   - {student_id} {full_name:<24} ({tag})")

    # ── Section B students ──
    print(f"\n>> Section B students")
    for i, (student_id, full_name) in enumerate(SECTION_B_STUDENTS):
        student = await _ensure_student(
            session, student_id=student_id, full_name=full_name,
        )
        reg = await _ensure_registration(
            session, student=student, term=term, section=section_b,
            status_=RegistrationStatus.REGISTERED,
        )
        if i == MOVER_INDEX:
            # Cohort is B, but they moved CS101 to A's slot.
            # The original B RegistrationCourse is marked dropped
            # AND a StudentScheduleAddition row points at A's slot.
            await _ensure_registration_course(
                session, registration=reg, course=course, is_dropped=True,
            )
            await _ensure_schedule_addition(
                session,
                registration=reg, slot=slot_a_cs101,
                course=course, source_section=section_a,
            )
            print(f"   - {student_id} {full_name:<24} "
                  f"(MOVED CS101 B→A: ADDED on A's roster, absent from B's)")
        else:
            await _ensure_registration_course(
                session, registration=reg, course=course, is_dropped=False,
            )
            print(f"   - {student_id} {full_name:<24} (ORIGINAL)")

    await session.commit()
    print("\n✅ Seed complete.\n")
    print("Expected roster for Section A × CS101:")
    print("   total=5  (4 originals + 1 added)")
    print("   ORIGINAL (4): Abel, Bethel, Chala, Dawit")
    print("                 (Eyerusalem dropped → excluded;")
    print("                  Feven is still draft → excluded)")
    print("   ADDED    (1): Kalkidan (is_added_via_drop=True, from Section B)")
    print()
    print("Expected roster for Section B × CS101:")
    print("   total=4  (all originals; Kalkidan moved out, none added in)")
    print("   ORIGINAL: Genet, Hana, Ibrahim, Jemberu")
    print()
    print("Expected GET /me/sections (instructor, this term): 3 entries")
    print("   - Section A × CS101  (slot_count=3)")
    print("   - Section A × MATH101 (slot_count=1)")
    print("   - Section B × CS101  (slot_count=2)")


async def main() -> None:
    engine = create_async_engine(DATABASE_URL, echo=False, future=True)
    AsyncSessionLocal = sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False,
    )
    async with AsyncSessionLocal() as session:
        await seed(session)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
