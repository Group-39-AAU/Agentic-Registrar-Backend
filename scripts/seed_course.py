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
from app.modules.course.models import (
    AcademicTerm, Course, CoursePrerequisite,
)


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


async def seed() -> None:
    engine = create_async_engine(DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        await _seed_term(session)
        courses_by_code = await _seed_courses(session)
        await _seed_prerequisites(session, courses_by_code)

    await engine.dispose()
    print("\n🎉 Course Management Phase 0 seed complete.")


if __name__ == "__main__":
    asyncio.run(seed())
