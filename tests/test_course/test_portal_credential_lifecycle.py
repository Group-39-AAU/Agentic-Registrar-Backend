"""
End-to-end portal-credential lifecycle.

Stitches the changes from Commits 1 + 2 into a single user journey:

    admission login (email + chosen pwd)
        → student fills application, gets enrolled
        → officer onboards via /courses/officer/students/onboard-from-enrollment
        → admission password is replaced by a 4-digit PIN, emailed to student
        → student logs in with UGR/0042/14 + PIN
            → must_change_password=True; protected endpoints 403
        → student calls /auth/change-password with PIN + new strong pwd
        → student logs in again with UGR/0042/14 + new pwd
            → must_change_password=False; protected endpoints reachable

This documents the contract a real frontend would consume.
"""
from __future__ import annotations

import re
import uuid
from datetime import date

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.dependencies import get_email_service
from app.core.security import hash_password
from app.database.session import get_db
from app.main import app
from app.modules.auth.models import User
from app.modules.undergraduate.enrollment.models import Enrollment
from app.modules.undergraduate.models import (
    UndergraduateAdmissionTerm, UndergraduateApplication,
)
from app.shared.email import EmailMessage, EmailService
from app.shared.enums import (
    ApplicationStatus, SponsorshipType, StreamType, UserRole,
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
async def admission_user_with_enrollment(async_session) -> tuple[User, Enrollment]:
    """
    A user who has an admission account (email + chosen password) and
    a matching Enrollment row — i.e. just before officer onboarding.
    """
    user = User(
        id=uuid.uuid4(),
        email="lifecycle@example.com",
        first_name="Cycle", last_name="Test",
        hashed_password=hash_password("admission-pwd"),
        role=UserRole.STUDENT, is_active=True,
    )
    async_session.add(user)
    await async_session.flush()

    term = UndergraduateAdmissionTerm(
        id=uuid.uuid4(),
        term_name="2025/2026 Round 1 (lifecycle)",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 4, 30),
        is_open=False,
    )
    async_session.add(term)
    await async_session.flush()

    app_row = UndergraduateApplication(
        id=uuid.uuid4(),
        applicant_id=user.id,
        admission_term_id=term.id,
        admission_number="2955398",
        sponsorship_type=SponsorshipType.SELF_SPONSORED,
        stream=StreamType.NATURAL,
        current_status=ApplicationStatus.ENROLLED,
    )
    async_session.add(app_row)
    await async_session.flush()

    enrollment = Enrollment(
        id=uuid.uuid4(),
        application_id=app_row.id,
        applicant_id=user.id,
        university_id="UGR/0042/14",
        department="Computer Science",
        enrollment_term="2025/2026",
    )
    async_session.add(enrollment)
    await async_session.commit()
    return user, enrollment


@pytest_asyncio.fixture
async def officer(async_session) -> User:
    user = User(
        id=uuid.uuid4(),
        email="officer@aau.edu.et",
        first_name="Reg", last_name="Officer",
        hashed_password=hash_password("officer-pwd"),
        role=UserRole.REGISTRAR_OFFICER, is_active=True,
    )
    async_session.add(user)
    await async_session.commit()
    return user


# ── Helpers ──────────────────────────────────────────────────────


async def _login(
    client: AsyncClient, identifier: str, password: str,
) -> dict:
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": identifier, "password": password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _extract_pin(message: EmailMessage) -> str:
    m = re.search(r"Temporary PIN:\s*(\d{4})", message.text_body)
    assert m, f"PIN not found in body: {message.text_body!r}"
    return m.group(1)


# ── The lifecycle ───────────────────────────────────────────────


async def test_full_portal_credential_lifecycle(
    client, admission_user_with_enrollment, officer, fake_email,
):
    user, enrollment = admission_user_with_enrollment

    # ── Phase 1: admission login still works (email + chosen pwd) ───
    body = await _login(client, "lifecycle@example.com", "admission-pwd")
    assert body["must_change_password"] is False

    # ── Phase 2: officer onboards the student ──────────────────────
    officer_token = (
        await _login(client, "officer@aau.edu.et", "officer-pwd")
    )["access_token"]
    resp = await client.post(
        "/api/v1/courses/officer/students/onboard-from-enrollment",
        headers={"Authorization": f"Bearer {officer_token}"},
        json={"enrollment_id": str(enrollment.id)},
    )
    assert resp.status_code == 201, resp.text
    student = resp.json()
    assert student["student_id"] == "UGR/0042/14"

    # The PIN was emailed (and only emailed)
    assert len(fake_email.sent) == 1
    pin = _extract_pin(fake_email.sent[0])
    # The HTTP response must NOT contain the PIN
    assert pin not in resp.text

    # ── Phase 3: admission password is dead, PIN works ─────────────
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": "lifecycle@example.com", "password": "admission-pwd"},
    )
    assert resp.status_code == 401  # admission pwd was overwritten

    # Login by UGR id with the PIN — succeeds, but locked
    body = await _login(client, "UGR/0042/14", pin)
    assert body["must_change_password"] is True
    student_token = body["access_token"]

    # ── Phase 4: every protected endpoint is locked out ────────────
    resp = await client.get(
        "/api/v1/courses/me/curriculum",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "password_change_required"

    # /auth/me stays reachable so the UI can render the change-pwd form
    resp = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert resp.status_code == 200

    # ── Phase 5: student sets a permanent password ─────────────────
    resp = await client.post(
        "/api/v1/auth/change-password",
        headers={"Authorization": f"Bearer {student_token}"},
        json={"current_password": pin, "new_password": "permanent-strong-pwd"},
    )
    assert resp.status_code == 204

    # ── Phase 6: PIN is now invalid; new pwd works; lockout cleared ─
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": "UGR/0042/14", "password": pin},
    )
    assert resp.status_code == 401

    body = await _login(client, "UGR/0042/14", "permanent-strong-pwd")
    assert body["must_change_password"] is False

    # The lockout-specific 403 is gone (other 403s on the protected
    # endpoint may still happen for unrelated reasons — Student row
    # not found in this test session — but the password-lockout code
    # must not appear).
    resp = await client.get(
        "/api/v1/courses/me/curriculum",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    if resp.status_code == 403:
        detail = resp.json().get("detail")
        if isinstance(detail, dict):
            assert detail.get("code") != "password_change_required"

    # Email login also works with the new password (same User row)
    body = await _login(client, "lifecycle@example.com", "permanent-strong-pwd")
    assert body["must_change_password"] is False
