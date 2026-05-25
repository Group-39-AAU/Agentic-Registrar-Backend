"""
Track A — two-step scheduling HTTP flow.

Verifies the split endpoints:

  - ``POST /officer/sections/allocate`` runs phase 1 only. After it,
    every REGISTERED student in the department has ``section_id``
    set but no ``ClassScheduleSlot`` rows have been emitted.

  - ``POST /officer/schedule/generate`` runs phase 2 only. The
    department's already-allocated sections gain weekly slot rows
    whose total hours per course match ``course.credit_hours``.

  - Calling phase 2 without phase 1 first returns an empty timetable
    (no sections to schedule), not an error.

  - Role gating: a non-officer caller is rejected with 403.
"""
from __future__ import annotations

import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.security import hash_password
from app.database.session import get_db
from app.main import app
from app.modules.auth.models import User
from app.modules.course.models import (
    ClassScheduleSlot, Classroom, Course, CourseManagementOfficer,
    Registration, ScheduleConflict, Section, Student,
)
from app.modules.programs.models import AcademicProgram
from app.shared.enums import (
    EnrollmentStatus, OfficerRole, RegistrationStatus, ScheduleConflictStatus,
    ScheduleConflictType, SponsorshipType, StreamType, UserRole,
)


# ── Fixtures ────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def client(async_session) -> AsyncClient:
    async def _override_db():
        yield async_session
    app.dependency_overrides[get_db] = _override_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def officer(async_session) -> User:
    """
    Scheduling-authority test actor. Scheduling permission moved from
    plain registrar officers to Department Heads, so this fixture
    pairs the underlying User (role=REGISTRAR_OFFICER) with a
    CourseManagementOfficer row whose role is DEPARTMENT_HEAD.
    """
    user = User(
        id=uuid.uuid4(),
        email="sched-officer@aau.edu.et",
        first_name="Sched", last_name="Officer",
        hashed_password=hash_password("officer-pwd"),
        role=UserRole.REGISTRAR_OFFICER, is_active=True,
    )
    async_session.add(user)
    await async_session.flush()
    async_session.add(
        CourseManagementOfficer(
            id=uuid.uuid4(),
            user_id=user.id,
            staff_id="REG/SCHED/01",
            role=OfficerRole.DEPARTMENT_HEAD,
            # Must match cs_world's "Computer Science" department —
            # scheduling auth checks the DH's department against the
            # requested department now.
            department="Computer Science",
            authorization_level=5,
        )
    )
    await async_session.commit()
    return user


@pytest_asyncio.fixture
async def cs_world(async_session, seeded_term) -> dict:
    """
    Build a tiny but complete CS-sem-1 world: a course, a classroom
    in the department's inventory, a REGISTERED student, and an
    AcademicProgram whose ``department`` matches what the agent keys
    off (so the officer endpoint can resolve ``program_id``).
    """
    program = AcademicProgram(
        code="CS-T2", name="Computer Science",
        department="Computer Science",
        stream=StreamType.NATURAL, is_active=True,
    )
    async_session.add(program)
    await async_session.flush()

    course = Course(
        code="CS101", title="Intro Programming",
        credit_hours=3, semester=1, department="Computer Science",
    )
    async_session.add(course)
    await async_session.flush()

    room = Classroom(
        name="CS-LECT-1", capacity=80,
        department="Computer Science",
    )
    async_session.add(room)
    await async_session.flush()

    user = User(
        id=uuid.uuid4(),
        email="cs-sem1-student@aau.edu.et",
        first_name="CS", last_name="Student",
        hashed_password=hash_password("x"),
        role=UserRole.STUDENT, is_active=True,
    )
    async_session.add(user)
    await async_session.flush()
    student = Student(
        user_id=user.id,
        student_id="UGR/0500/14",
        full_name="CS Student",
        current_semester=1,
        department="Computer Science",
        enrollment_status=EnrollmentStatus.ACTIVE,
    )
    async_session.add(student)
    await async_session.flush()

    reg = Registration(
        student_id=student.id,
        term_id=seeded_term.id,
        status=RegistrationStatus.REGISTERED,
        sponsorship_type=SponsorshipType.SELF_SPONSORED,
    )
    async_session.add(reg)
    await async_session.commit()
    return {
        "term": seeded_term, "course": course,
        "student": student, "registration": reg,
        "program": program,
    }


async def _login(client: AsyncClient, identifier: str, password: str) -> str:
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": identifier, "password": password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


# ── Phase 1 only: allocate ──────────────────────────────────────


async def test_allocate_endpoint_creates_section_without_slots(
    client, officer, cs_world, async_session,
):
    token = await _login(client, officer.email, "officer-pwd")

    resp = await client.post(
        "/api/v1/courses/officer/sections/allocate",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "term_id": str(cs_world["term"].id),
            "program_id": str(cs_world["program"].id),
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["department"] == "Computer Science"
    assert body["students_placed_count"] == 1
    assert len(body["sections_created"]) == 1

    # The cohort section exists with the student pinned to it
    sections = (
        await async_session.execute(
            select(Section).where(Section.term_id == cs_world["term"].id)
        )
    ).scalars().all()
    assert len(sections) == 1
    reg = (
        await async_session.execute(
            select(Registration).where(
                Registration.student_id == cs_world["student"].id,
            )
        )
    ).scalar_one()
    assert reg.section_id == sections[0].id

    # No ClassScheduleSlot rows yet — phase 2 hasn't run
    slots = (
        await async_session.execute(select(ClassScheduleSlot))
    ).scalars().all()
    assert slots == []


# ── Phase 2 only: timetable ─────────────────────────────────────


async def test_generate_endpoint_without_allocation_returns_422(
    client, officer, cs_world,
):
    """
    Calling /schedule/generate before /sections/allocate must surface a
    clear 422 instead of silently returning an empty timetable — the
    officer needs to know nothing was generated and why.
    """
    token = await _login(client, officer.email, "officer-pwd")

    resp = await client.post(
        "/api/v1/courses/officer/schedule/generate",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "term_id": str(cs_world["term"].id),
            "program_id": str(cs_world["program"].id),
        },
    )
    assert resp.status_code == 422, resp.text
    assert "allocate" in resp.json()["detail"].lower()


# ── Full two-step sequence ──────────────────────────────────────


async def test_two_step_flow_produces_sections_then_slots(
    client, officer, cs_world, async_session,
):
    token = await _login(client, officer.email, "officer-pwd")

    # Step 1
    r1 = await client.post(
        "/api/v1/courses/officer/sections/allocate",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "term_id": str(cs_world["term"].id),
            "program_id": str(cs_world["program"].id),
        },
    )
    assert r1.status_code == 200
    assert r1.json()["students_placed_count"] == 1

    # Step 2
    r2 = await client.post(
        "/api/v1/courses/officer/schedule/generate",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "term_id": str(cs_world["term"].id),
            "program_id": str(cs_world["program"].id),
        },
    )
    assert r2.status_code == 200
    body = r2.json()
    assert body["section_count"] == 1
    # 3 weekly hours for CS101 (a 3-credit course)
    assert body["slots_created"] == cs_world["course"].credit_hours

    # Slots exist in the DB
    slots = (
        await async_session.execute(select(ClassScheduleSlot))
    ).scalars().all()
    assert len(slots) == cs_world["course"].credit_hours


# ── Role gating ─────────────────────────────────────────────────


async def test_allocate_rejects_non_officer(client, cs_world, async_session):
    """A logged-in student gets 403 on the allocate endpoint."""
    # Promote the cs_world student to a real login by setting a known pwd
    student_user = await async_session.get(User, cs_world["student"].user_id)
    student_user.hashed_password = hash_password("student-pwd")
    await async_session.commit()

    token = await _login(client, student_user.email, "student-pwd")
    resp = await client.post(
        "/api/v1/courses/officer/sections/allocate",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "term_id": str(cs_world["term"].id),
            "program_id": str(cs_world["program"].id),
        },
    )
    assert resp.status_code == 403


async def test_reallocation_wipes_and_rebuilds(
    client, officer, cs_world, async_session,
):
    """
    Re-running /sections/allocate must wipe the prior allocation
    (sections + Registration.section_id pins + class slots) and
    rebuild from scratch, not no-op. Officers use the same endpoint
    both to allocate and to redo allocation.
    """
    token = await _login(client, officer.email, "officer-pwd")

    # First run lays down section A
    r1 = await client.post(
        "/api/v1/courses/officer/sections/allocate",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "term_id": str(cs_world["term"].id),
            "program_id": str(cs_world["program"].id),
        },
    )
    assert r1.status_code == 200
    assert r1.json()["students_placed_count"] == 1
    first_section_ids = {
        s["section_id"] for s in r1.json()["sections_created"]
    }

    # Build a slot pinned to that section so we can confirm the wipe
    # also tears down any timetable already generated.
    section_id = next(iter(first_section_ids))
    sec_uuid = uuid.UUID(section_id)
    from datetime import time as dtime
    async_session.add(
        ClassScheduleSlot(
            section_id=sec_uuid,
            course_id=cs_world["course"].id,
            day_of_week="MON",
            start_time=dtime(8, 30),
            end_time=dtime(9, 30),
        )
    )
    # Also seed a stale ScheduleConflict pointing at the same section
    # — it FKs to sections.id without ON DELETE CASCADE, so the wipe
    # has to delete it explicitly or the Section delete fails with a
    # ForeignKeyViolationError.
    async_session.add(
        ScheduleConflict(
            term_id=cs_world["term"].id,
            department="Computer Science",
            conflict_type=ScheduleConflictType.ROOM_DOUBLE_BOOKED,
            section_id=sec_uuid,
            description="stale, should be wiped",
            detected_by_agent_id="AGENT_ASA_TEST",
            status=ScheduleConflictStatus.OPEN,
        )
    )
    await async_session.commit()

    # Second run on a stable population must rebuild rather than no-op
    r2 = await client.post(
        "/api/v1/courses/officer/sections/allocate",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "term_id": str(cs_world["term"].id),
            "program_id": str(cs_world["program"].id),
        },
    )
    assert r2.status_code == 200
    body = r2.json()
    assert body["students_placed_count"] == 1, (
        "Re-run must re-place the student, not skip as 'already placed'."
    )
    second_section_ids = {
        s["section_id"] for s in body["sections_created"]
    }
    assert second_section_ids and second_section_ids.isdisjoint(first_section_ids), (
        "Re-run must create fresh section rows, not reuse the wiped ones."
    )

    # Old slot is gone, old sections are gone
    remaining_slots = (
        await async_session.execute(select(ClassScheduleSlot))
    ).scalars().all()
    assert remaining_slots == []
    remaining_sections = (
        await async_session.execute(
            select(Section).where(
                Section.id.in_([uuid.UUID(s) for s in first_section_ids]),
                Section.is_deleted == False,  # noqa: E712
            )
        )
    ).scalars().all()
    assert remaining_sections == []
    remaining_conflicts = (
        await async_session.execute(
            select(ScheduleConflict).where(
                ScheduleConflict.section_id.in_(
                    [uuid.UUID(s) for s in first_section_ids],
                ),
            )
        )
    ).scalars().all()
    assert remaining_conflicts == []


async def test_section_codes_restart_per_semester(
    client, officer, cs_world, async_session,
):
    """
    Inside the same (term, department), sections in different
    semesters get codes restarting at ``A``. A sem-3 student in the
    same department + term gets section "A" in sem-3, not "B".
    """
    # Add a second student in sem 3 (same department, same term)
    user2 = User(
        id=uuid.uuid4(),
        email="sem3-cs@aau.edu.et",
        first_name="S3", last_name="Stud",
        hashed_password=hash_password("x"),
        role=UserRole.STUDENT, is_active=True,
    )
    async_session.add(user2)
    await async_session.flush()
    student2 = Student(
        user_id=user2.id,
        student_id="UGR/0501/12",
        full_name="Sem3 Stud",
        current_semester=3,
        department="Computer Science",
        enrollment_status=EnrollmentStatus.ACTIVE,
    )
    async_session.add(student2)
    await async_session.flush()
    async_session.add(
        Registration(
            student_id=student2.id,
            term_id=cs_world["term"].id,
            status=RegistrationStatus.REGISTERED,
            sponsorship_type=SponsorshipType.SELF_SPONSORED,
        )
    )
    await async_session.commit()

    token = await _login(client, officer.email, "officer-pwd")
    resp = await client.post(
        "/api/v1/courses/officer/sections/allocate",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "term_id": str(cs_world["term"].id),
            "program_id": str(cs_world["program"].id),
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    by_sem = {s["semester"]: s["section_code"] for s in body["sections_created"]}
    assert by_sem[1] == "A"
    assert by_sem[3] == "A"


async def test_generate_rejects_non_officer(client, cs_world, async_session):
    student_user = await async_session.get(User, cs_world["student"].user_id)
    student_user.hashed_password = hash_password("student-pwd")
    await async_session.commit()

    token = await _login(client, student_user.email, "student-pwd")
    resp = await client.post(
        "/api/v1/courses/officer/schedule/generate",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "term_id": str(cs_world["term"].id),
            "program_id": str(cs_world["program"].id),
        },
    )
    assert resp.status_code == 403
