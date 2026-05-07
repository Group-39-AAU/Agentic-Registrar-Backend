"""
Phase 0 — seed_course.py idempotency contract.

The seed script has skip-if-exists guards on every block. Re-running
it against a populated database must not create duplicates and must
not raise. We exercise the underlying ``_seed_*`` helpers directly
(rather than the ``seed()`` runner that owns its own engine) so the
test runs against the in-memory SQLite session our conftest provides.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

from sqlalchemy import select

from app.modules.course.models import (
    AcademicTerm, Course, CoursePrerequisite, CourseManagementOfficer,
    CourseOffering, Instructor, InstructorAssignment, Section, Student,
)


def _load_seed_module():
    """
    Load scripts/seed_course.py as a module without running its
    asyncio entry point.
    """
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    seed_path = repo_root / "scripts" / "seed_course.py"
    spec = importlib.util.spec_from_file_location("seed_course", seed_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("seed_course", module)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


async def _run_seed_helpers(session, seed):
    terms = await seed._seed_terms(session)
    courses = await seed._seed_courses(session)
    await seed._seed_prerequisites(session, courses)
    instructors = await seed._seed_instructors(session)
    # Each term gets its own offerings + sections so a Fall-registered
    # student can't accidentally end up in a Spring section.
    for term in terms:
        await seed._seed_offerings_and_sections(
            session, term, courses, instructors,
        )
    await seed._seed_students(session)
    await seed._seed_officers(session)


def _row_counts(session):
    """
    Snapshot row counts of every Phase-0 table that the seed touches.
    Section is intentionally excluded — under the cohort model, sections
    are emitted by the AcademicSchedulingAgent at allocation time, not
    at seed time, so the seed legitimately produces zero of them.
    """
    async def _inner():
        out = {}
        for name, model in (
            ("terms", AcademicTerm),
            ("courses", Course),
            ("prereqs", CoursePrerequisite),
            ("instructors", Instructor),
            ("offerings", CourseOffering),
            ("assignments", InstructorAssignment),
            ("students", Student),
            ("officers", CourseManagementOfficer),
        ):
            rows = (await session.execute(select(model))).scalars().all()
            out[name] = len(rows)
        return out
    return _inner()


async def test_seed_helpers_are_idempotent(async_session):
    seed = _load_seed_module()

    # First run should populate every block
    await _run_seed_helpers(async_session, seed)
    first = await _row_counts(async_session)

    # Sanity: every table has at least one row after the first run
    assert all(v > 0 for v in first.values()), first

    # Second run must not raise and must not change row counts
    await _run_seed_helpers(async_session, seed)
    second = await _row_counts(async_session)

    assert first == second, (
        "Seed helpers must be idempotent — row counts changed between "
        f"first run {first} and second run {second}"
    )


async def test_seeded_course_count_matches_module_constant(async_session):
    """The seeded course catalog should match what the script declares."""
    seed = _load_seed_module()
    await _run_seed_helpers(async_session, seed)

    courses = (await async_session.execute(select(Course))).scalars().all()
    assert len(courses) == len(seed.COURSES)


async def test_seeded_prereq_count_matches_module_constant(async_session):
    seed = _load_seed_module()
    await _run_seed_helpers(async_session, seed)

    rows = (
        await async_session.execute(select(CoursePrerequisite))
    ).scalars().all()
    assert len(rows) == len(seed.PREREQUISITES)


async def test_seed_does_not_create_sections(async_session):
    """
    Under the cohort-section model, sections are created by the
    AcademicSchedulingAgent at allocation time (when the officer runs
    /courses/officer/schedule/generate). The seed only sets up the
    catalog + offerings + students; cohort sections appear later.
    """
    seed = _load_seed_module()
    await _run_seed_helpers(async_session, seed)

    sections = (await async_session.execute(select(Section))).scalars().all()
    assert len(sections) == 0


async def test_seeded_officer_roles_include_a_department_head(async_session):
    """
    SRS §3.5 requires a DEPARTMENT_HEAD account to test the
    prerequisite-override path. The seed script must produce one.
    """
    from app.shared.enums import OfficerRole
    seed = _load_seed_module()
    await _run_seed_helpers(async_session, seed)

    rows = (
        await async_session.execute(
            select(CourseManagementOfficer).where(
                CourseManagementOfficer.role == OfficerRole.DEPARTMENT_HEAD
            )
        )
    ).scalars().all()
    assert len(rows) >= 1
