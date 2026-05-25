"""
Track A — Classroom model invariants + seed sanity.

Verifies the per-department physical-room inventory is queryable and
honours the schema constraints (positive capacity, unique name).
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.modules.course.models import Classroom


# ── Construction + read ─────────────────────────────────────────


async def test_create_classroom_round_trips(async_session):
    room = Classroom(
        name="TEST-101",
        capacity=40,
        department="Software Engineering",
    )
    async_session.add(room)
    await async_session.commit()

    fetched = (
        await async_session.execute(
            select(Classroom).where(Classroom.name == "TEST-101")
        )
    ).scalar_one()
    assert fetched.capacity == 40
    assert fetched.department == "Software Engineering"


# ── Constraints ─────────────────────────────────────────────────


async def test_classroom_capacity_must_be_positive(async_session):
    bad = Classroom(
        name="ZERO-CAP",
        capacity=0,                      # CHECK: > 0
        department="Software Engineering",
    )
    async_session.add(bad)
    with pytest.raises(IntegrityError):
        await async_session.flush()


async def test_classroom_name_is_unique(async_session):
    a = Classroom(name="DUP-101", capacity=50, department="Software Engineering")
    async_session.add(a)
    await async_session.flush()

    b = Classroom(name="DUP-101", capacity=70, department="Civil Engineering")
    async_session.add(b)
    with pytest.raises(IntegrityError):
        await async_session.flush()


# ── Seed sanity ─────────────────────────────────────────────────


@pytest_asyncio.fixture
async def seeded_classrooms(async_session):
    """Run only the classroom block of seed_course_management.py against the test DB."""
    import importlib.util
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "seed_course_management",
        repo_root / "scripts" / "seed_course_management.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    await module._seed_classrooms(async_session)
    return module


async def test_seed_produces_classrooms_for_every_department(
    async_session, seeded_classrooms,
):
    rooms = (
        await async_session.execute(select(Classroom))
    ).scalars().all()
    by_dept: dict[str, list[Classroom]] = {}
    for r in rooms:
        by_dept.setdefault(r.department, []).append(r)

    expected_depts = {
        "Software Engineering",
        "Electrical Engineering",
        "Chemical Engineering",
        "Civil Engineering",
        "Mechanical Engineering",
        "Bio Medical Engineering",
    }
    assert set(by_dept) == expected_depts
    # Every department has at least one room
    assert all(len(rs) >= 1 for rs in by_dept.values())


async def test_seed_classroom_count_matches_module_constant(
    async_session, seeded_classrooms,
):
    rooms = (
        await async_session.execute(select(Classroom))
    ).scalars().all()
    assert len(rooms) == len(seeded_classrooms.CLASSROOMS)


async def test_se_has_the_largest_lecture_hall(
    async_session, seeded_classrooms,
):
    """SE owns the 80-seat hall to fit the bulk SE-sem1 cohort."""
    biggest = (
        await async_session.execute(
            select(Classroom).order_by(Classroom.capacity.desc()).limit(1)
        )
    ).scalar_one()
    assert biggest.department == "Software Engineering"
    assert biggest.capacity == 80


async def test_seed_classrooms_idempotent(async_session, seeded_classrooms):
    """Re-running the seed function must not duplicate rows."""
    first = (
        await async_session.execute(select(Classroom))
    ).scalars().all()

    await seeded_classrooms._seed_classrooms(async_session)

    second = (
        await async_session.execute(select(Classroom))
    ).scalars().all()
    assert len(first) == len(second)
