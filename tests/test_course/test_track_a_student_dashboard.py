"""
Track A — student dashboard endpoint tests.

Covers ``RegistrationService.get_student_dashboard`` plus the HTTP
contract of ``GET /api/v1/courses/me``:

  - Happy path: student with department + sponsorship + open term +
    registration + cohort section → every field populated.
  - No open term → current_term is None.
  - Open term but no registration → current_term filled, but
    registration_status and section are both None.
  - Registration without an allocated section → current_term.section
    is None but registration_status is set.
  - Unknown student id raises EntityNotFoundError.
  - HTTP: a non-student User gets 403.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.security import hash_password
from app.database.session import get_db
from app.main import app
from app.modules.auth.models import User
from app.modules.course.exceptions import EntityNotFoundError
from app.modules.course.models import (
    AcademicTerm, Registration, Section, Student,
)
from app.modules.course.service import RegistrationService
from app.shared.enums import (
    EnrollmentStatus, RegistrationStatus, SponsorshipType, UserRole,
)


# ── Service-layer tests ────────────────────────────────────────


@pytest_asyncio.fixture
async def fully_populated_student(async_session, seeded_term) -> Student:
    """
    Student with non-null department + sponsorship_type, registered in
    the (open) seeded_term and allocated to a cohort Section. Every
    optional field on the dashboard payload should be non-null.
    """
    user = User(
        id=uuid.uuid4(),
        email="dashtest@aau.edu.et",
        first_name="Dash", last_name="Tester",
        hashed_password=hash_password("x"),
        role=UserRole.STUDENT, is_active=True,
    )
    async_session.add(user)
    await async_session.flush()

    student = Student(
        user_id=user.id,
        student_id="UGR/0042/14",
        full_name="Dash Tester",
        current_semester=2,
        department="Software Engineering",
        sponsorship_type=SponsorshipType.GOVERNMENT,
        enrollment_status=EnrollmentStatus.ACTIVE,
    )
    async_session.add(student)
    await async_session.flush()

    section = Section(
        term_id=seeded_term.id,
        department="Software Engineering",
        semester=2,
        section_code="A",
        room="NB-101",
        capacity=60,
        enrolled_count=1,
    )
    async_session.add(section)
    await async_session.flush()

    reg = Registration(
        student_id=student.id,
        term_id=seeded_term.id,
        status=RegistrationStatus.REGISTERED,
        sponsorship_type=SponsorshipType.GOVERNMENT,
        section_id=section.id,
    )
    async_session.add(reg)
    await async_session.flush()
    return student


async def test_dashboard_happy_path_returns_every_field(
    async_session, fully_populated_student, seeded_term,
):
    svc = RegistrationService(async_session)
    payload = await svc.get_student_dashboard(fully_populated_student.id)

    assert payload["student_id"] == "UGR/0042/14"
    assert payload["full_name"] == "Dash Tester"
    assert payload["email"] == "dashtest@aau.edu.et"
    assert payload["department"] == "Software Engineering"
    assert payload["current_semester"] == 2
    assert payload["sponsorship_type"] == SponsorshipType.GOVERNMENT
    assert payload["enrollment_status"] == EnrollmentStatus.ACTIVE

    term = payload["current_term"]
    assert term is not None
    assert term["term_id"] == seeded_term.id
    assert term["term_name"] == seeded_term.term_name
    assert term["registration_status"] == RegistrationStatus.REGISTERED

    section = term["section"]
    assert section is not None
    assert section["section_code"] == "A"
    assert section["room"] == "NB-101"
    assert section["capacity"] == 60


async def test_dashboard_with_no_open_term_returns_null_current_term(
    async_session, fully_populated_student, seeded_term,
):
    seeded_term.is_open = False
    await async_session.flush()

    svc = RegistrationService(async_session)
    payload = await svc.get_student_dashboard(fully_populated_student.id)

    assert payload["current_term"] is None
    # Identity fields are still populated
    assert payload["student_id"] == "UGR/0042/14"
    assert payload["department"] == "Software Engineering"


async def test_dashboard_with_open_term_but_no_registration(
    async_session, seeded_term,
):
    """
    Student exists, term is open, but the student has no Registration
    in this term yet — current_term is populated but registration_status
    and section are both None.
    """
    user = User(
        id=uuid.uuid4(),
        email="noreg@aau.edu.et",
        first_name="No", last_name="Reg",
        hashed_password=hash_password("x"),
        role=UserRole.STUDENT, is_active=True,
    )
    async_session.add(user)
    await async_session.flush()
    student = Student(
        user_id=user.id,
        student_id="UGR/0099/14",
        full_name="No Reg",
        current_semester=1,
        department="Computer Science",
        sponsorship_type=SponsorshipType.SELF_SPONSORED,
        enrollment_status=EnrollmentStatus.ACTIVE,
    )
    async_session.add(student)
    await async_session.flush()

    svc = RegistrationService(async_session)
    payload = await svc.get_student_dashboard(student.id)

    term = payload["current_term"]
    assert term is not None
    assert term["term_name"] == seeded_term.term_name
    assert term["registration_status"] is None
    assert term["section"] is None


async def test_dashboard_with_registration_but_no_section(
    async_session, seeded_term,
):
    """
    Officer hasn't run scheduling yet — registration exists with no
    section_id. current_term.section is None but registration_status
    is the actual state.
    """
    user = User(
        id=uuid.uuid4(),
        email="nosec@aau.edu.et",
        first_name="No", last_name="Sec",
        hashed_password=hash_password("x"),
        role=UserRole.STUDENT, is_active=True,
    )
    async_session.add(user)
    await async_session.flush()
    student = Student(
        user_id=user.id,
        student_id="UGR/0088/14",
        full_name="No Sec",
        current_semester=1,
        department="Computer Science",
        sponsorship_type=SponsorshipType.SELF_SPONSORED,
        enrollment_status=EnrollmentStatus.ACTIVE,
    )
    async_session.add(student)
    await async_session.flush()
    reg = Registration(
        student_id=student.id,
        term_id=seeded_term.id,
        status=RegistrationStatus.REGISTRATION_OPEN,
        sponsorship_type=SponsorshipType.SELF_SPONSORED,
        section_id=None,
    )
    async_session.add(reg)
    await async_session.flush()

    svc = RegistrationService(async_session)
    payload = await svc.get_student_dashboard(student.id)

    term = payload["current_term"]
    assert term is not None
    assert term["registration_status"] == RegistrationStatus.REGISTRATION_OPEN
    assert term["section"] is None


async def test_dashboard_unknown_student_raises_404(async_session):
    svc = RegistrationService(async_session)
    with pytest.raises(EntityNotFoundError, match="Student"):
        await svc.get_student_dashboard(uuid.uuid4())


# ── HTTP-level tests ──────────────────────────────────────────


@pytest_asyncio.fixture
async def client(async_session) -> AsyncClient:
    async def _override_db():
        yield async_session
    app.dependency_overrides[get_db] = _override_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _login(client: AsyncClient, identifier: str, password: str) -> str:
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": identifier, "password": password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def test_get_me_http_returns_dashboard(
    client, async_session, fully_populated_student, seeded_term,
):
    """End-to-end: login as the student, hit /me, expect the payload."""
    user = await async_session.get(User, fully_populated_student.user_id)
    user.hashed_password = hash_password("dashpwd")
    await async_session.commit()

    token = await _login(client, "dashtest@aau.edu.et", "dashpwd")
    resp = await client.get(
        "/api/v1/courses/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["student_id"] == "UGR/0042/14"
    assert body["full_name"] == "Dash Tester"
    assert body["email"] == "dashtest@aau.edu.et"
    assert body["department"] == "Software Engineering"
    assert body["current_semester"] == 2
    assert body["sponsorship_type"] == "GOVERNMENT"
    assert body["current_term"] is not None
    assert body["current_term"]["section"]["section_code"] == "A"


async def test_get_me_http_403_for_non_student(
    client, async_session,
):
    """A logged-in officer (not a student) should get 403 from /me."""
    user = User(
        id=uuid.uuid4(),
        email="officer-dash@aau.edu.et",
        first_name="Reg", last_name="Officer",
        hashed_password=hash_password("officer-pwd"),
        role=UserRole.REGISTRAR_OFFICER, is_active=True,
    )
    async_session.add(user)
    await async_session.commit()

    token = await _login(client, "officer-dash@aau.edu.et", "officer-pwd")
    resp = await client.get(
        "/api/v1/courses/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
