"""
Track A — OnboardingService portal-credential issuance.

Verifies the credential lifecycle attached to onboarding:

  1. A 4-digit PIN is generated (cryptographically random).
  2. ``User.hashed_password`` is replaced with the PIN's bcrypt hash.
  3. ``User.must_change_password`` is set True.
  4. The plaintext PIN is sent via the email service exactly once
     to the student's registered email.
  5. The plaintext PIN is *not* persisted, *not* logged, *not*
     returned in the HTTP response, and *not* in the audit metadata.
  6. Email-delivery failures do not roll back onboarding.

Uses an in-memory fake EmailService — no real Brevo calls.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

import pytest
import pytest_asyncio

from app.core.security import generate_temporary_pin, verify_password
from app.modules.auth.models import User
from app.modules.course.models import Student
from app.modules.course.service import OnboardingService
from app.modules.undergraduate.enrollment.models import Enrollment
from app.shared.email import EmailMessage, EmailService
from app.shared.enums import UserRole


# ── Fakes ────────────────────────────────────────────────────────


class FakeEmailService(EmailService):
    """Captures sends in memory; never touches a network."""

    def __init__(self, *, raise_on_send: bool = False) -> None:
        # Skip parent __init__ — provider is irrelevant here
        self.sent: list[EmailMessage] = []
        self._raise = raise_on_send

    async def send(self, message: EmailMessage) -> None:  # type: ignore[override]
        if self._raise:
            raise RuntimeError("simulated SMTP outage")
        self.sent.append(message)


# ── Fixtures ─────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def applicant(async_session) -> User:
    """
    User in the post-admission, pre-onboarding state: they have an
    admission account but no Student row and no portal PIN yet.
    """
    user = User(
        id=uuid.uuid4(),
        email="ugr-0042-14@aau.edu.et",
        first_name="Sara", last_name="Tesfaye",
        hashed_password="<<original-admission-hash>>",
        role=UserRole.STUDENT, is_active=True,
        must_change_password=False,
    )
    async_session.add(user)
    await async_session.commit()
    return user


@pytest_asyncio.fixture
async def enrollment(async_session, applicant) -> Enrollment:
    """An Enrollment row pointing at the applicant — what onboarding consumes."""
    from app.modules.undergraduate.models import (
        UndergraduateAdmissionTerm, UndergraduateApplication,
    )
    from app.shared.enums import (
        ApplicationStatus, SponsorshipType, StreamType,
    )
    from datetime import date

    term = UndergraduateAdmissionTerm(
        id=uuid.uuid4(),
        term_name="2025/2026 Round 1",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 4, 30),
        is_open=False,
    )
    async_session.add(term)
    await async_session.flush()

    app_row = UndergraduateApplication(
        id=uuid.uuid4(),
        applicant_id=applicant.id,
        admission_term_id=term.id,
        admission_number="2955397",
        sponsorship_type=SponsorshipType.SELF_SPONSORED,
        stream=StreamType.NATURAL,
        current_status=ApplicationStatus.ENROLLED,
    )
    async_session.add(app_row)
    await async_session.flush()

    enrollment = Enrollment(
        id=uuid.uuid4(),
        application_id=app_row.id,
        applicant_id=applicant.id,
        university_id="UGR/0042/14",
        portal_password="legacy-unused",
        department="Computer Science",
        section="A",
        enrollment_term="2025/2026",
    )
    async_session.add(enrollment)
    await async_session.commit()
    return enrollment


@pytest.fixture
def fake_email() -> FakeEmailService:
    return FakeEmailService()


# ── PIN generator unit tests ─────────────────────────────────────


def test_generate_temporary_pin_is_four_digits():
    for _ in range(50):
        pin = generate_temporary_pin()
        assert re.fullmatch(r"\d{4}", pin), f"unexpected PIN shape: {pin!r}"


def test_generate_temporary_pin_supports_longer_lengths():
    pin = generate_temporary_pin(digits=6)
    assert re.fullmatch(r"\d{6}", pin)


def test_generate_temporary_pin_rejects_too_few_digits():
    with pytest.raises(ValueError, match="at least 4"):
        generate_temporary_pin(digits=3)


def test_generate_temporary_pin_preserves_leading_zeros():
    """Statistically: among 200 PINs, expect ~20 starting with '0'."""
    leading_zero = sum(
        1 for _ in range(200) if generate_temporary_pin().startswith("0")
    )
    assert leading_zero > 0, "PIN generator should produce some leading-zero PINs"


# ── Onboarding wires the PIN end-to-end ──────────────────────────


async def test_onboarding_overwrites_password_with_hashed_pin(
    async_session, applicant, enrollment, fake_email,
):
    svc = OnboardingService(async_session, email_service=fake_email)
    await svc.onboard_student_from_enrollment(
        enrollment_id=enrollment.id,
        officer_role=UserRole.REGISTRAR_OFFICER,
        officer_id=uuid.uuid4(),
    )
    await async_session.refresh(applicant)
    assert applicant.hashed_password != "<<original-admission-hash>>"
    assert applicant.must_change_password is True


async def test_onboarding_sends_credentials_email_with_pin(
    async_session, applicant, enrollment, fake_email,
):
    svc = OnboardingService(async_session, email_service=fake_email)
    await svc.onboard_student_from_enrollment(
        enrollment_id=enrollment.id,
        officer_role=UserRole.REGISTRAR_OFFICER,
        officer_id=uuid.uuid4(),
    )

    assert len(fake_email.sent) == 1
    msg = fake_email.sent[0]
    assert msg.to_email == "ugr-0042-14@aau.edu.et"
    assert "UGR/0042/14" in msg.text_body
    pin_match = re.search(r"Temporary PIN:\s*(\d{4})", msg.text_body)
    assert pin_match, f"PIN not found in email body: {msg.text_body}"
    pin = pin_match.group(1)

    # Verify the emailed PIN actually authenticates against the hash
    await async_session.refresh(applicant)
    assert verify_password(pin, applicant.hashed_password)


async def test_onboarding_does_not_leak_pin_into_response_or_audit(
    async_session, applicant, enrollment, fake_email, caplog,
):
    """Plaintext PIN must not appear in the Student response or any log."""
    svc = OnboardingService(async_session, email_service=fake_email)
    student = await svc.onboard_student_from_enrollment(
        enrollment_id=enrollment.id,
        officer_role=UserRole.REGISTRAR_OFFICER,
        officer_id=uuid.uuid4(),
    )
    pin = re.search(r"Temporary PIN:\s*(\d{4})", fake_email.sent[0].text_body).group(1)

    # The Student response object should not contain the PIN anywhere
    student_repr = repr(vars(student))
    assert pin not in student_repr

    # Captured logs (audit + general) should not contain the PIN either
    assert pin not in caplog.text


async def test_onboarding_succeeds_even_when_email_fails(
    async_session, applicant, enrollment,
):
    """SMTP outages cannot reverse an enrollment."""
    flaky_email = FakeEmailService(raise_on_send=True)
    svc = OnboardingService(async_session, email_service=flaky_email)

    student = await svc.onboard_student_from_enrollment(
        enrollment_id=enrollment.id,
        officer_role=UserRole.REGISTRAR_OFFICER,
        officer_id=uuid.uuid4(),
    )

    assert student.student_id == "UGR/0042/14"
    await async_session.refresh(applicant)
    assert applicant.must_change_password is True


async def test_onboarding_works_without_email_service_at_all(
    async_session, applicant, enrollment,
):
    """Email is optional — service-only callers (tests, scripts) skip it."""
    svc = OnboardingService(async_session)  # no email_service
    student = await svc.onboard_student_from_enrollment(
        enrollment_id=enrollment.id,
        officer_role=UserRole.REGISTRAR_OFFICER,
        officer_id=uuid.uuid4(),
    )
    await async_session.refresh(applicant)
    assert student.student_id == "UGR/0042/14"
    # PIN still issued even with no email — auditor may need to reset
    # it manually
    assert applicant.must_change_password is True
