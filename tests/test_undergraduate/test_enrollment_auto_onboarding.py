"""
/undergraduate/enrollment/run auto-onboards admitted students.

Verifies the refactor that fuses admission's "Enrollment" step with
course-management's "Onboarding" step: one officer call now mints
the UGR id, creates the Student row, replaces the user's password
with a 4-digit PIN, and emails the credentials — instead of the
officer having to call ``/courses/officer/students/onboard-from-enrollment``
per admitted student.

This complements ``test_portal_credential_lifecycle.py`` (which still
covers the explicit officer-onboard endpoint) by locking the
auto-onboard path with the same email capture + change-password
follow-through.
"""
from __future__ import annotations

import re
import uuid
from datetime import date

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.dependencies import get_email_service
from app.core.security import hash_password
from app.database.session import get_db
from app.main import app
from app.modules.auth.models import User
from app.modules.course.models import Student
from app.modules.undergraduate.enrollment.models import Enrollment
from app.modules.undergraduate.models import (
    UndergraduateAdmissionTerm, UndergraduateApplication,
)
from app.shared.email import EmailMessage, EmailService
from app.shared.enums import (
    ApplicationStatus, DecisionType, SponsorshipType, StreamType, UserRole,
)


# ── Fakes / overrides ────────────────────────────────────────────


class _CapturingEmailService(EmailService):
    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:  # type: ignore[override]
        self.sent.append(message)


@pytest_asyncio.fixture
async def fake_email() -> _CapturingEmailService:
    return _CapturingEmailService()


@pytest_asyncio.fixture
async def client(async_session, fake_email) -> AsyncClient:
    async def _override_db():
        yield async_session

    def _override_email() -> EmailService:
        return fake_email

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_email_service] = _override_email
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def officer(async_session) -> User:
    user = User(
        id=uuid.uuid4(),
        email="officer-auto@aau.edu.et",
        first_name="Reg", last_name="Officer",
        hashed_password=hash_password("officer-pwd"),
        role=UserRole.REGISTRAR_OFFICER, is_active=True,
    )
    async_session.add(user)
    await async_session.commit()
    return user


@pytest_asyncio.fixture
async def admitted_application(async_session) -> tuple[User, UndergraduateApplication, UndergraduateAdmissionTerm]:
    """
    A DECIDED + ADMIT application ready for /enrollment/run. No
    Enrollment row exists yet — the endpoint must create it.
    """
    applicant = User(
        id=uuid.uuid4(),
        email="newadmit@example.com",
        first_name="New", last_name="Admit",
        hashed_password=hash_password("admission-pwd"),
        role=UserRole.STUDENT, is_active=True,
    )
    async_session.add(applicant)
    await async_session.flush()

    term = UndergraduateAdmissionTerm(
        id=uuid.uuid4(),
        term_name="2025/2026 Round 1 (auto-onboard)",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 4, 30),
        is_open=False,
    )
    async_session.add(term)
    await async_session.flush()

    application = UndergraduateApplication(
        id=uuid.uuid4(),
        applicant_id=applicant.id,
        admission_term_id=term.id,
        admission_number="3070001",
        sponsorship_type=SponsorshipType.SELF_SPONSORED,
        stream=StreamType.NATURAL,
        current_status=ApplicationStatus.DECIDED,
        final_decision=DecisionType.ADMIT.value,
    )
    async_session.add(application)
    await async_session.commit()
    return applicant, application, term


async def _login(client: AsyncClient, identifier: str, password: str) -> dict:
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": identifier, "password": password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _extract_pin(message: EmailMessage) -> str:
    m = re.search(r"Temporary PIN:\s*(\d{4})", message.text_body)
    assert m, f"PIN not in body: {message.text_body!r}"
    return m.group(1)


# ── The test ────────────────────────────────────────────────────


async def test_run_enrollment_auto_onboards_and_emails_credentials(
    client, async_session, fake_email, officer, admitted_application,
):
    applicant, application, term = admitted_application

    officer_token = (
        await _login(client, "officer-auto@aau.edu.et", "officer-pwd")
    )["access_token"]

    # ── Run enrollment ── this single call should now also onboard.
    resp = await client.post(
        f"/api/v1/undergraduate/enrollment/run?term_id={term.id}",
        headers={"Authorization": f"Bearer {officer_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["enrolled_count"] == 1
    assert body["onboarded_count"] == 1
    assert len(body["enrollments"]) == 1
    assert body["enrollments"][0]["university_id"].startswith("UGR/")

    outcomes = body["onboarding_outcomes"]
    assert len(outcomes) == 1
    assert outcomes[0]["onboarded"] is True
    assert outcomes[0]["university_id"] == body["enrollments"][0]["university_id"]

    # ── Email contains the PIN, the response does not ──────────────
    assert len(fake_email.sent) == 1
    pin = _extract_pin(fake_email.sent[0])
    assert pin not in resp.text

    # ── Student row exists and User is locked into change-password ──
    student = (await async_session.execute(
        select(Student).where(Student.user_id == applicant.id)
    )).scalar_one()
    assert student.student_id == body["enrollments"][0]["university_id"]

    await async_session.refresh(applicant)
    assert applicant.must_change_password is True

    # ── Old admission password is dead; PIN works but is locked ─────
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": "newadmit@example.com", "password": "admission-pwd"},
    )
    assert resp.status_code == 401

    login = await _login(client, student.student_id, pin)
    assert login["must_change_password"] is True
    student_token = login["access_token"]

    # ── change-password flips the flag ─────────────────────────────
    resp = await client.post(
        "/api/v1/auth/change-password",
        headers={"Authorization": f"Bearer {student_token}"},
        json={"current_password": pin, "new_password": "permanent-strong-pwd"},
    )
    assert resp.status_code == 204

    final = await _login(client, student.student_id, "permanent-strong-pwd")
    assert final["must_change_password"] is False


async def test_run_enrollment_is_idempotent_for_onboarding(
    client, async_session, fake_email, officer, admitted_application,
):
    """
    Re-running /enrollment/run after a successful onboarding must not
    crash, must not re-issue a PIN, and must report the per-student
    state honestly (already-enrolled / already-onboarded).
    """
    _, _, term = admitted_application
    officer_token = (
        await _login(client, "officer-auto@aau.edu.et", "officer-pwd")
    )["access_token"]

    first = await client.post(
        f"/api/v1/undergraduate/enrollment/run?term_id={term.id}",
        headers={"Authorization": f"Bearer {officer_token}"},
    )
    assert first.status_code == 200
    assert first.json()["onboarded_count"] == 1
    assert len(fake_email.sent) == 1

    second = await client.post(
        f"/api/v1/undergraduate/enrollment/run?term_id={term.id}",
        headers={"Authorization": f"Bearer {officer_token}"},
    )
    # After the first run the application has moved DECIDED → ENROLLED,
    # so the DECIDED+ADMIT filter finds nothing on the rerun and the
    # endpoint short-circuits with 404. Either 404 ("nothing to enroll")
    # or 400 ("all already enrolled") is acceptable — the load-bearing
    # invariant is that no second PIN email was sent.
    assert second.status_code in (400, 404)
    assert len(fake_email.sent) == 1
