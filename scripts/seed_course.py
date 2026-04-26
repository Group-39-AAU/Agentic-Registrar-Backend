"""
Course Management — Phase 0 seed data.

Populates the deterministic sandbox that every track (A/B/C) reuses
in its own tests so feature work in any one track can be demoed
without dependencies on the others.

Idempotent: running twice does not create duplicates.

Usage:
    cd /path/to/project
    source venv/bin/activate
    python scripts/seed_course.py

Phase 0 seeds (this script):
    1. One open AcademicTerm  ("Fall 2026")
    2. 20 Courses across 4 departments with deterministic UUIDs
    3. ≥8 prerequisite edges spanning 3+ depth levels

Subsequent commits in Phase 0 will extend this script with:
    4. 10 Instructors + CourseOfferings + 100 Sections
    5. 30 Students across semesters 1–8
    6. Registrar Officer + Department Head accounts
"""

import asyncio
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.security import hash_password
from app.modules.auth.models import User
from app.modules.course.models import (
    AcademicTerm, Course, CoursePrerequisite, CourseOffering, Section,
    Instructor, InstructorAssignment, Student,
)
from app.shared.enums import EnrollmentStatus, UserRole


DATABASE_URL = str(settings.DATABASE_URL)


# ══════════════════════════════════════════════════════════════
#  Deterministic UUIDs
# ══════════════════════════════════════════════════════════════
# Using uuid5 with a fixed namespace so every run of the seed gets
# the same IDs. This means tests can hard-code "Fall 2026" =
# COURSE_NS:term:fall-2026 and look it up reliably.

_NS = uuid.UUID("c0a3e000-0000-4000-8000-000000000000")  # "course phase 0"


def _uid(*parts: str) -> uuid.UUID:
    """Stable UUID derived from the slash-joined parts."""
    return uuid.uuid5(_NS, "/".join(parts))


# ══════════════════════════════════════════════════════════════
#  Academic Term
# ══════════════════════════════════════════════════════════════

ACTIVE_TERM = {
    "id": _uid("term", "fall-2026"),
    "term_name": "Fall 2026",
    "start_date": date(2026, 9, 1),
    "end_date": date(2027, 1, 31),
    "is_open": True,
    "description": "Phase-0 sandbox term — deterministic seed data for "
                   "Course Management tracks A/B/C.",
}


# ══════════════════════════════════════════════════════════════
#  Courses (20 across 4 departments)
# ══════════════════════════════════════════════════════════════
# (code, title, credit_hours, semester, department)

COURSES = [
    # ── Computer Science (CS): 6 courses, semesters 1–6 ─────────
    ("CS101", "Introduction to Programming",        4, 1, "Computer Science"),
    ("CS201", "Data Structures",                    4, 2, "Computer Science"),
    ("CS202", "Discrete Mathematics",               3, 2, "Computer Science"),
    ("CS301", "Algorithms",                         4, 3, "Computer Science"),
    ("CS401", "Operating Systems",                  4, 4, "Computer Science"),
    ("CS501", "Distributed Systems",                3, 5, "Computer Science"),

    # ── Software Engineering (SE): 5 courses, semesters 2–6 ─────
    ("SE201", "Software Engineering Principles",    3, 2, "Software Engineering"),
    ("SE301", "Software Architecture",              3, 3, "Software Engineering"),
    ("SE401", "Software Testing",                   3, 4, "Software Engineering"),
    ("SE402", "Project Management",                 2, 4, "Software Engineering"),
    ("SE501", "DevOps & Continuous Delivery",       3, 5, "Software Engineering"),

    # ── Mathematics (MATH): 5 courses, semesters 1–4 ────────────
    ("MATH101", "Calculus I",                       4, 1, "Mathematics"),
    ("MATH102", "Calculus II",                      4, 2, "Mathematics"),
    ("MATH201", "Linear Algebra",                   3, 2, "Mathematics"),
    ("MATH301", "Probability & Statistics",         3, 3, "Mathematics"),
    ("MATH401", "Numerical Methods",                3, 4, "Mathematics"),

    # ── Economics (ECON): 4 courses, semesters 1–4 ──────────────
    ("ECON101", "Microeconomics",                   3, 1, "Economics"),
    ("ECON102", "Macroeconomics",                   3, 2, "Economics"),
    ("ECON201", "Econometrics",                     3, 3, "Economics"),
    ("ECON301", "Development Economics",            3, 4, "Economics"),
]


# ══════════════════════════════════════════════════════════════
#  Prerequisites
# ══════════════════════════════════════════════════════════════
# (course_code, prerequisite_course_code)
# Designed to span at least 3 depth levels so Track A's compliance
# agent has non-trivial cases to validate against.

PREREQUISITES = [
    # CS chain: 101 -> 201 -> 301 -> 401 -> 501
    ("CS201", "CS101"),
    ("CS301", "CS201"),
    ("CS301", "CS202"),
    ("CS401", "CS301"),
    ("CS501", "CS401"),

    # SE chain: 201 -> 301 -> 401 -> 501
    ("SE301", "SE201"),
    ("SE401", "SE301"),
    ("SE501", "SE401"),

    # MATH chain: 101 -> 102 -> 301 -> 401; 201 prereqs MATH101
    ("MATH102", "MATH101"),
    ("MATH201", "MATH101"),
    ("MATH301", "MATH102"),
    ("MATH401", "MATH301"),

    # Cross-department: ECON201 needs MATH301 (statistics)
    ("ECON201", "ECON101"),
    ("ECON201", "MATH301"),
    ("ECON301", "ECON201"),
]


# ══════════════════════════════════════════════════════════════
#  Instructors (10 across the 4 departments)
# ══════════════════════════════════════════════════════════════
# (instructor_id, first_name, last_name, department)

INSTRUCTORS = [
    ("STAFF/0001/10", "Alemayehu", "Bekele",   "Computer Science"),
    ("STAFF/0002/10", "Bethel",    "Tadesse",  "Computer Science"),
    ("STAFF/0003/10", "Chala",     "Mekonnen", "Software Engineering"),
    ("STAFF/0004/10", "Dawit",     "Girma",    "Software Engineering"),
    ("STAFF/0005/10", "Eyerusalem","Kassa",    "Mathematics"),
    ("STAFF/0006/10", "Feven",     "Asfaw",    "Mathematics"),
    ("STAFF/0007/10", "Getachew",  "Lemma",    "Economics"),
    ("STAFF/0008/10", "Hanna",     "Negussie", "Economics"),
    ("STAFF/0009/10", "Iskinder",  "Worku",    "Computer Science"),
    ("STAFF/0010/10", "Jemal",     "Hussein",  "Mathematics"),
]


# Per-course offering capacity and section count.
# 5 sections per course, capacity 30 per section -> 150 total seats.

OFFERING_CAPACITY = 150
SECTIONS_PER_COURSE = 5
SECTION_CAPACITY = 30
TIME_SLOTS = [
    "MON 08:30-10:00, WED 08:30-10:00",
    "MON 10:30-12:00, WED 10:30-12:00",
    "TUE 13:30-15:00, THU 13:30-15:00",
    "TUE 15:30-17:00, THU 15:30-17:00",
    "FRI 08:30-11:30",
]
ROOMS = ["NB-101", "NB-102", "NB-203", "NB-204", "NB-305", "FBE-12", "FBE-14"]


# ══════════════════════════════════════════════════════════════
#  Students (30 across semesters 1–8)
# ══════════════════════════════════════════════════════════════
# (student_id, first_name, last_name, current_semester)
# Spread across semesters 1–8 so Track A's compliance agent has
# students at multiple curriculum depths to validate against.

STUDENTS = [
    ("UGR/0001/14", "Abel",      "Tesfaye",   1),
    ("UGR/0002/14", "Beza",      "Worku",     1),
    ("UGR/0003/14", "Caleb",     "Mulugeta",  1),
    ("UGR/0004/14", "Dina",      "Hailemariam", 1),
    ("UGR/0005/14", "Ermias",    "Bekele",    2),
    ("UGR/0006/14", "Frehiwot",  "Asrat",     2),
    ("UGR/0007/14", "Gemechu",   "Olana",     2),
    ("UGR/0008/14", "Helen",     "Yohannes",  2),
    ("UGR/0009/14", "Isaac",     "Demeke",    3),
    ("UGR/0010/14", "Jerusalem", "Tilahun",   3),
    ("UGR/0011/14", "Kalkidan",  "Sisay",     3),
    ("UGR/0012/14", "Lidya",     "Abebe",     3),
    ("UGR/0013/14", "Marcos",    "Negash",    4),
    ("UGR/0014/14", "Nardos",    "Birhanu",   4),
    ("UGR/0015/14", "Obse",      "Tariku",    4),
    ("UGR/0016/14", "Petros",    "Selam",     4),
    ("UGR/0017/14", "Rahel",     "Yilma",     5),
    ("UGR/0018/14", "Samuel",    "Habte",     5),
    ("UGR/0019/14", "Tigist",    "Mekuria",   5),
    ("UGR/0020/14", "Ujulu",     "Gobena",    5),
    ("UGR/0021/14", "Veronica",  "Eshete",    6),
    ("UGR/0022/14", "Wondwossen","Aklilu",    6),
    ("UGR/0023/14", "Xavier",    "Birru",     6),
    ("UGR/0024/14", "Yared",     "Lemessa",   6),
    ("UGR/0025/14", "Zewditu",   "Asfaw",     7),
    ("UGR/0026/14", "Amanuel",   "Getaneh",   7),
    ("UGR/0027/14", "Bisrat",    "Kebede",    7),
    ("UGR/0028/14", "Christian", "Wolde",     8),
    ("UGR/0029/14", "Daniel",    "Tamirat",   8),
    ("UGR/0030/14", "Eleni",     "Berhanu",   8),
]


# ══════════════════════════════════════════════════════════════
#  Runner


# ══════════════════════════════════════════════════════════════
#  Runner
# ══════════════════════════════════════════════════════════════

async def _seed_term(session: AsyncSession) -> AcademicTerm:
    existing = (
        await session.execute(
            select(AcademicTerm).where(AcademicTerm.term_name == ACTIVE_TERM["term_name"])
        )
    ).scalar_one_or_none()
    if existing:
        print(f"⚠️  Academic term '{ACTIVE_TERM['term_name']}' already exists — skipping.")
        return existing

    term = AcademicTerm(**ACTIVE_TERM)
    session.add(term)
    await session.commit()
    print(f"✅ Seeded academic term: {term.term_name} (open={term.is_open}).")
    return term


async def _seed_courses(session: AsyncSession) -> dict[str, Course]:
    by_code: dict[str, Course] = {}
    rows = (await session.execute(select(Course))).scalars().all()
    by_code.update({c.code: c for c in rows})

    new_count = 0
    for code, title, credit_hours, semester, department in COURSES:
        if code in by_code:
            continue
        course = Course(
            id=_uid("course", code),
            code=code,
            title=title,
            credit_hours=credit_hours,
            semester=semester,
            department=department,
        )
        session.add(course)
        by_code[code] = course
        new_count += 1

    await session.commit()
    if new_count:
        print(f"✅ Seeded {new_count} courses (catalog total: {len(by_code)}).")
    else:
        print(f"⚠️  All {len(by_code)} courses already present — skipping.")
    return by_code


async def _seed_prerequisites(
    session: AsyncSession,
    courses_by_code: dict[str, Course],
) -> None:
    existing = (
        await session.execute(select(CoursePrerequisite))
    ).scalars().all()
    existing_pairs = {(p.course_id, p.prerequisite_course_id) for p in existing}

    new_count = 0
    for course_code, prereq_code in PREREQUISITES:
        course = courses_by_code[course_code]
        prereq = courses_by_code[prereq_code]
        pair = (course.id, prereq.id)
        if pair in existing_pairs:
            continue
        session.add(
            CoursePrerequisite(
                id=_uid("prereq", course_code, prereq_code),
                course_id=course.id,
                prerequisite_course_id=prereq.id,
            )
        )
        new_count += 1

    await session.commit()
    if new_count:
        print(f"✅ Seeded {new_count} prerequisite edges (catalog total: {len(existing) + new_count}).")
    else:
        print(f"⚠️  All {len(existing)} prerequisite edges already present — skipping.")


async def _ensure_user(
    session: AsyncSession,
    *,
    email: str,
    first_name: str,
    last_name: str,
    role: UserRole,
    user_uid: uuid.UUID,
) -> User:
    """Idempotent helper: get-or-create a User row for a seeded actor."""
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


async def _seed_instructors(
    session: AsyncSession,
) -> dict[str, Instructor]:
    by_staff_id: dict[str, Instructor] = {}
    rows = (await session.execute(select(Instructor))).scalars().all()
    by_staff_id.update({i.instructor_id: i for i in rows})

    new_count = 0
    for staff_id, first_name, last_name, department in INSTRUCTORS:
        if staff_id in by_staff_id:
            continue
        # The staff_id contains '/' which is not legal in an email
        # local-part, so derive a slug for the email instead.
        slug = staff_id.lower().replace("/", "-")
        user = await _ensure_user(
            session,
            email=f"{slug}@aau.edu.et",
            first_name=first_name,
            last_name=last_name,
            role=UserRole.AGENT,  # instructor accounts treated as staff
            user_uid=_uid("user", "instructor", staff_id),
        )
        instructor = Instructor(
            id=_uid("instructor", staff_id),
            user_id=user.id,
            instructor_id=staff_id,
            department=department,
        )
        session.add(instructor)
        by_staff_id[staff_id] = instructor
        new_count += 1

    await session.commit()
    if new_count:
        print(f"✅ Seeded {new_count} instructors (catalog total: {len(by_staff_id)}).")
    else:
        print(f"⚠️  All {len(by_staff_id)} instructors already present — skipping.")
    return by_staff_id


async def _seed_offerings_and_sections(
    session: AsyncSession,
    term: AcademicTerm,
    courses_by_code: dict[str, Course],
    instructors_by_staff_id: dict[str, Instructor],
) -> None:
    instructors_by_dept: dict[str, list[Instructor]] = {}
    for ins in instructors_by_staff_id.values():
        instructors_by_dept.setdefault(ins.department, []).append(ins)

    existing_offerings = (
        await session.execute(
            select(CourseOffering).where(CourseOffering.term_id == term.id)
        )
    ).scalars().all()
    existing_offering_courses = {o.course_id for o in existing_offerings}

    offering_count = 0
    section_count = 0
    assignment_count = 0
    for code, course in courses_by_code.items():
        if course.id in existing_offering_courses:
            continue

        offering = CourseOffering(
            id=_uid("offering", term.term_name, code),
            course_id=course.id,
            term_id=term.id,
            capacity=OFFERING_CAPACITY,
            section_count=SECTIONS_PER_COURSE,
        )
        session.add(offering)
        offering_count += 1

        # Create 5 sections, round-robining the department's instructors.
        dept_instructors = instructors_by_dept.get(course.department, [])
        for i in range(SECTIONS_PER_COURSE):
            instructor = (
                dept_instructors[i % len(dept_instructors)]
                if dept_instructors else None
            )
            section_code = chr(ord("A") + i)
            session.add(
                Section(
                    id=_uid("section", term.term_name, code, section_code),
                    offering_id=offering.id,
                    section_code=section_code,
                    room=ROOMS[i % len(ROOMS)],
                    time_slot=TIME_SLOTS[i % len(TIME_SLOTS)],
                    instructor_id=instructor.id if instructor else None,
                    capacity=SECTION_CAPACITY,
                    enrolled_count=0,
                )
            )
            section_count += 1

            if instructor is not None:
                # Idempotency: only add the assignment if it doesn't already exist.
                existing_assn = (
                    await session.execute(
                        select(InstructorAssignment).where(
                            InstructorAssignment.instructor_id == instructor.id,
                            InstructorAssignment.course_id == course.id,
                            InstructorAssignment.term_id == term.id,
                        )
                    )
                ).scalar_one_or_none()
                if existing_assn is None:
                    session.add(
                        InstructorAssignment(
                            id=_uid("assn", term.term_name, instructor.instructor_id, code),
                            instructor_id=instructor.id,
                            course_id=course.id,
                            term_id=term.id,
                        )
                    )
                    assignment_count += 1

    await session.commit()
    if offering_count or section_count:
        print(
            f"✅ Seeded {offering_count} offerings, {section_count} sections, "
            f"{assignment_count} instructor assignments."
        )
    else:
        print(f"⚠️  Offerings for term '{term.term_name}' already present — skipping.")


async def _seed_students(session: AsyncSession) -> None:
    existing = (await session.execute(select(Student))).scalars().all()
    by_student_id = {s.student_id: s for s in existing}

    new_count = 0
    for student_id, first_name, last_name, semester in STUDENTS:
        if student_id in by_student_id:
            continue
        slug = student_id.lower().replace("/", "-")
        user = await _ensure_user(
            session,
            email=f"{slug}@aau.edu.et",
            first_name=first_name,
            last_name=last_name,
            role=UserRole.STUDENT,
            user_uid=_uid("user", "student", student_id),
        )
        session.add(
            Student(
                id=_uid("student", student_id),
                user_id=user.id,
                student_id=student_id,
                full_name=f"{first_name} {last_name}",
                current_semester=semester,
                enrollment_status=EnrollmentStatus.ACTIVE,
            )
        )
        new_count += 1

    await session.commit()
    if new_count:
        print(f"✅ Seeded {new_count} students (catalog total: {len(existing) + new_count}).")
    else:
        print(f"⚠️  All {len(existing)} students already present — skipping.")


async def seed() -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        term = await _seed_term(session)
        courses_by_code = await _seed_courses(session)
        await _seed_prerequisites(session, courses_by_code)
        instructors = await _seed_instructors(session)
        await _seed_offerings_and_sections(
            session, term, courses_by_code, instructors,
        )
        await _seed_students(session)

    await engine.dispose()
    print("\n🎉 Course Management Phase 0 seed complete.")


if __name__ == "__main__":
    asyncio.run(seed())
