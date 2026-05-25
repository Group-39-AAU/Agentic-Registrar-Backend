"""
Track A — extra scheduling coverage.

Covers the surfaces added in this round:

  - ``GET /api/v1/courses/instructors/me/schedule`` — instructor view
    of their own weekly schedule.
  - ``POST /api/v1/courses/officer/schedule/slots/{slot_id}/
    assign-instructor`` — officer pins a different instructor onto an
    existing slot, with a same-time collision check.
  - 422 short-circuits when ``allocate_sections`` runs against a
    department with no Classroom inventory, and when
    ``generate_timetable`` runs before any Section exists (the latter
    already lives in test_track_a_scheduling_two_step.py).
"""
from __future__ import annotations

import uuid
from datetime import time

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.security import hash_password
from app.database.session import get_db
from app.main import app
from app.modules.auth.models import User
from app.modules.course.models import (
    ClassScheduleSlot, Classroom, Course, CourseManagementOfficer,
    Instructor, Section,
)
from app.modules.programs.models import AcademicProgram
from app.shared.enums import OfficerRole, StreamType, UserRole


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
    Scheduling-authority test actor. Scheduling permission moved
    from plain registrar officers to Department Heads, so this
    fixture also seeds a CourseManagementOfficer row with
    role=DEPARTMENT_HEAD attached to the same user.
    """
    user = User(
        id=uuid.uuid4(),
        email="extras-officer@aau.edu.et",
        first_name="Ex", last_name="Officer",
        hashed_password=hash_password("officer-pwd"),
        role=UserRole.REGISTRAR_OFFICER, is_active=True,
    )
    async_session.add(user)
    await async_session.flush()
    async_session.add(
        CourseManagementOfficer(
            id=uuid.uuid4(),
            user_id=user.id,
            staff_id="REG/EXTRAS/01",
            role=OfficerRole.DEPARTMENT_HEAD,
            # Slots and sections in slot_world live under "Computer
            # Science" — DH must own that department.
            department="Computer Science",
            authorization_level=5,
        )
    )
    await async_session.commit()
    return user


async def _instructor(async_session, *, email: str, staff_id: str) -> Instructor:
    user = User(
        id=uuid.uuid4(),
        email=email,
        first_name="Ins", last_name="Tructor",
        hashed_password=hash_password("instr-pwd"),
        role=UserRole.INSTRUCTOR, is_active=True,
    )
    async_session.add(user)
    await async_session.flush()
    ins = Instructor(
        user_id=user.id,
        instructor_id=staff_id,
        department="Computer Science",
    )
    async_session.add(ins)
    await async_session.flush()
    return ins


@pytest_asyncio.fixture
async def slot_world(async_session, seeded_term) -> dict:
    """
    Build a minimal cohort for slot-reassign testing:

      - Two instructors (so we can swap from one to the other).
      - One Section in CS, sem 1.
      - One ClassScheduleSlot already pinned to instructor A on
        MON 08:30–09:30.
    """
    course = Course(
        code="CS101", title="Intro CS",
        credit_hours=1, semester=1, department="Computer Science",
    )
    async_session.add(course)
    await async_session.flush()

    room = Classroom(
        name="CS-LECT-1", capacity=80, department="Computer Science",
    )
    async_session.add(room)
    await async_session.flush()

    ins_a = await _instructor(
        async_session, email="ins-a@aau.edu.et", staff_id="STAFF/1001/16",
    )
    ins_b = await _instructor(
        async_session, email="ins-b@aau.edu.et", staff_id="STAFF/1002/16",
    )

    section = Section(
        term_id=seeded_term.id,
        department="Computer Science",
        semester=1,
        section_code="A",
        capacity=80,
        enrolled_count=0,
    )
    async_session.add(section)
    await async_session.flush()

    slot = ClassScheduleSlot(
        section_id=section.id,
        course_id=course.id,
        instructor_id=ins_a.id,
        day_of_week="MON",
        start_time=time(8, 30),
        end_time=time(9, 30),
    )
    async_session.add(slot)
    await async_session.commit()
    return {
        "term": seeded_term,
        "course": course,
        "section": section,
        "slot": slot,
        "ins_a": ins_a,
        "ins_b": ins_b,
    }


async def _login(client: AsyncClient, identifier: str, password: str) -> str:
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": identifier, "password": password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


# ── Slot-reassign endpoint ──────────────────────────────────────


async def test_officer_can_reassign_slot_instructor(
    client, officer, slot_world, async_session,
):
    token = await _login(client, officer.email, "officer-pwd")
    resp = await client.post(
        f"/api/v1/courses/officer/schedule/slots/"
        f"{slot_world['slot'].id}/assign-instructor",
        headers={"Authorization": f"Bearer {token}"},
        json={"instructor_id": str(slot_world["ins_b"].id)},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["instructor_id"] == str(slot_world["ins_b"].id)

    refreshed = (
        await async_session.execute(
            select(ClassScheduleSlot).where(
                ClassScheduleSlot.id == slot_world["slot"].id,
            )
        )
    ).scalar_one()
    assert refreshed.instructor_id == slot_world["ins_b"].id


async def test_reassign_rejects_collision_with_existing_booking(
    client, officer, slot_world, async_session,
):
    """
    Instructor B already teaches another section at MON 08:30 in the
    same term — reassigning the test slot onto B must 422 rather than
    silently double-booking them.
    """
    # Build a second section that already has B at MON 08:30
    other_section = Section(
        term_id=slot_world["term"].id,
        department="Computer Science",
        semester=1,
        section_code="B",
        capacity=80,
        enrolled_count=0,
    )
    async_session.add(other_section)
    await async_session.flush()
    other_slot = ClassScheduleSlot(
        section_id=other_section.id,
        course_id=slot_world["course"].id,
        instructor_id=slot_world["ins_b"].id,
        day_of_week="MON",
        start_time=time(8, 30),
        end_time=time(9, 30),
    )
    async_session.add(other_slot)
    await async_session.commit()

    token = await _login(client, officer.email, "officer-pwd")
    resp = await client.post(
        f"/api/v1/courses/officer/schedule/slots/"
        f"{slot_world['slot'].id}/assign-instructor",
        headers={"Authorization": f"Bearer {token}"},
        json={"instructor_id": str(slot_world["ins_b"].id)},
    )
    assert resp.status_code == 422, resp.text
    assert "already booked" in resp.json()["detail"].lower()

    # Slot keeps the original instructor — no partial mutation.
    refreshed = (
        await async_session.execute(
            select(ClassScheduleSlot).where(
                ClassScheduleSlot.id == slot_world["slot"].id,
            )
        )
    ).scalar_one()
    assert refreshed.instructor_id == slot_world["ins_a"].id


async def test_reassign_404s_on_unknown_slot(
    client, officer, slot_world,
):
    token = await _login(client, officer.email, "officer-pwd")
    resp = await client.post(
        f"/api/v1/courses/officer/schedule/slots/"
        f"{uuid.uuid4()}/assign-instructor",
        headers={"Authorization": f"Bearer {token}"},
        json={"instructor_id": str(slot_world["ins_b"].id)},
    )
    assert resp.status_code == 404


async def test_reassign_404s_on_unknown_instructor(
    client, officer, slot_world,
):
    token = await _login(client, officer.email, "officer-pwd")
    resp = await client.post(
        f"/api/v1/courses/officer/schedule/slots/"
        f"{slot_world['slot'].id}/assign-instructor",
        headers={"Authorization": f"Bearer {token}"},
        json={"instructor_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404


async def test_reassign_requires_officer(
    client, slot_world, async_session,
):
    """A student logging in must get 403 — not officer/admin."""
    student_user = User(
        id=uuid.uuid4(),
        email="non-officer@aau.edu.et",
        first_name="Stu", last_name="Dent",
        hashed_password=hash_password("stu-pwd"),
        role=UserRole.STUDENT, is_active=True,
    )
    async_session.add(student_user)
    await async_session.commit()

    token = await _login(client, student_user.email, "stu-pwd")
    resp = await client.post(
        f"/api/v1/courses/officer/schedule/slots/"
        f"{slot_world['slot'].id}/assign-instructor",
        headers={"Authorization": f"Bearer {token}"},
        json={"instructor_id": str(slot_world["ins_b"].id)},
    )
    assert resp.status_code == 403


# ── /instructors/me/schedule ────────────────────────────────────


async def test_instructor_me_schedule_returns_their_slots(
    client, slot_world, async_session,
):
    """The instructor sees the one slot they teach in the term."""
    user_a = await async_session.get(User, slot_world["ins_a"].user_id)
    user_a.hashed_password = hash_password("ins-a-pwd")
    await async_session.commit()
    token = await _login(client, user_a.email, "ins-a-pwd")

    resp = await client.get(
        "/api/v1/courses/instructors/me/schedule",
        params={"term_id": str(slot_world["term"].id)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["course_code"] == "CS101"
    assert rows[0]["day_of_week"] == "MON"


async def test_instructor_me_schedule_403_for_non_instructor(
    client, officer, slot_world,
):
    """A registrar officer (not an INSTRUCTOR) gets 403."""
    token = await _login(client, officer.email, "officer-pwd")
    resp = await client.get(
        "/api/v1/courses/instructors/me/schedule",
        params={"term_id": str(slot_world["term"].id)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


async def test_instructor_me_schedule_403_when_no_profile(
    client, async_session, slot_world,
):
    """
    A user with role=INSTRUCTOR but no matching Instructor row gets 403.
    """
    user = User(
        id=uuid.uuid4(),
        email="orphan-instructor@aau.edu.et",
        first_name="Or", last_name="Phan",
        hashed_password=hash_password("orphan-pwd"),
        role=UserRole.INSTRUCTOR, is_active=True,
    )
    async_session.add(user)
    await async_session.commit()
    token = await _login(client, user.email, "orphan-pwd")
    resp = await client.get(
        "/api/v1/courses/instructors/me/schedule",
        params={"term_id": str(slot_world["term"].id)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


# ── allocate / 422 when no classrooms ───────────────────────────


async def test_allocate_422_when_department_has_no_classrooms(
    client, seeded_term, async_session,
):
    """
    Calling /sections/allocate against a department with no Classroom
    rows must 422 with a clear message rather than silently producing
    an empty allocation. The program must exist (otherwise the
    program_id resolver 404s before the classroom check).

    The DH used here is scoped to "Empty Department" so the new
    department-scope auth check passes and the no-classrooms guard
    is what fires the 422.
    """
    empty_program = AcademicProgram(
        code="EMPTY-DEPT", name="Empty Department",
        department="Empty Department",
        stream=StreamType.NATURAL, is_active=True,
    )
    async_session.add(empty_program)

    empty_dept_dh = User(
        id=uuid.uuid4(),
        email="empty-dept-dh@aau.edu.et",
        first_name="Empty", last_name="DH",
        hashed_password=hash_password("dh-pwd"),
        role=UserRole.REGISTRAR_OFFICER, is_active=True,
    )
    async_session.add(empty_dept_dh)
    await async_session.flush()
    async_session.add(
        CourseManagementOfficer(
            id=uuid.uuid4(),
            user_id=empty_dept_dh.id,
            staff_id="REG/EMPTY/01",
            role=OfficerRole.DEPARTMENT_HEAD,
            department="Empty Department",
            authorization_level=5,
        )
    )
    await async_session.commit()

    token = await _login(client, empty_dept_dh.email, "dh-pwd")
    resp = await client.post(
        "/api/v1/courses/officer/sections/allocate",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "term_id": str(seeded_term.id),
            "program_id": str(empty_program.id),
        },
    )
    assert resp.status_code == 422, resp.text
    assert "classrooms" in resp.json()["detail"].lower()


async def test_allocate_404_when_program_id_unknown(
    client, officer, seeded_term,
):
    """An unknown program_id must surface a 404 before any service work."""
    token = await _login(client, officer.email, "officer-pwd")
    resp = await client.post(
        "/api/v1/courses/officer/sections/allocate",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "term_id": str(seeded_term.id),
            "program_id": str(uuid.uuid4()),
        },
    )
    assert resp.status_code == 404, resp.text
    assert "AcademicProgram" in resp.json()["detail"]
