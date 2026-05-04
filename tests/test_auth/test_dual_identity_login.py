"""
Auth — dual-identity login (Commit 1 of portal-credential lifecycle).

Real Ethiopian university portals (AAU included) accept *either* the
admission-time email or the post-enrollment UGR ID at the same login
endpoint. AuthService.authenticate must:

  - Resolve email → User directly.
  - Resolve UGR student_id → User via the Student.user_id join.
  - Treat ``must_change_password`` as part of the response (the
    /auth/login route surfaces it so the client knows to prompt the
    student to set a permanent password).

These tests hit the service directly to keep them fast and focused;
the full HTTP flow is exercised in the Commit-3 lifecycle tests.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from app.core.security import hash_password
from app.modules.auth.models import User
from app.modules.auth.service import AuthService
from app.modules.course.models import Student
from app.shared.enums import EnrollmentStatus, UserRole


@pytest_asyncio.fixture
async def applicant_user(async_session) -> User:
    """User who only has an admission account — no Student row yet."""
    user = User(
        id=uuid.uuid4(),
        email="applicant@example.com",
        first_name="App", last_name="Licant",
        hashed_password=hash_password("admission-pwd"),
        role=UserRole.STUDENT, is_active=True,
    )
    async_session.add(user)
    await async_session.commit()
    return user


@pytest_asyncio.fixture
async def enrolled_student(async_session) -> tuple[User, Student]:
    """User + Student row, mimicking a post-onboarding state."""
    user = User(
        id=uuid.uuid4(),
        email="ugr-0001-14@aau.edu.et",
        first_name="Abel", last_name="Tesfaye",
        hashed_password=hash_password("portal-pwd"),
        role=UserRole.STUDENT, is_active=True,
    )
    async_session.add(user)
    await async_session.flush()
    student = Student(
        user_id=user.id,
        student_id="UGR/0001/14",
        full_name="Abel Tesfaye",
        current_semester=1,
        enrollment_status=EnrollmentStatus.ACTIVE,
    )
    async_session.add(student)
    await async_session.commit()
    return user, student


# ── Email-based login (admission-era applicants) ────────────────


async def test_login_by_email_returns_token_and_flag(
    async_session, applicant_user,
):
    svc = AuthService(async_session)
    token, must_change = await svc.authenticate(
        "applicant@example.com", "admission-pwd",
    )
    assert token
    assert must_change is False


async def test_login_by_email_wrong_password_rejected(
    async_session, applicant_user,
):
    svc = AuthService(async_session)
    with pytest.raises(ValueError, match="Invalid"):
        await svc.authenticate("applicant@example.com", "nope")


# ── Student-ID-based login (post-onboarding portal users) ───────


async def test_login_by_student_id_returns_token(
    async_session, enrolled_student,
):
    svc = AuthService(async_session)
    token, must_change = await svc.authenticate(
        "UGR/0001/14", "portal-pwd",
    )
    assert token
    assert must_change is False


async def test_login_by_student_id_with_email_password_still_works(
    async_session, enrolled_student,
):
    """The same User row backs both paths — the password is shared."""
    svc = AuthService(async_session)
    token_email, _ = await svc.authenticate(
        "ugr-0001-14@aau.edu.et", "portal-pwd",
    )
    token_id, _ = await svc.authenticate("UGR/0001/14", "portal-pwd")
    assert token_email and token_id


async def test_login_with_unknown_identifier_rejected(async_session):
    svc = AuthService(async_session)
    with pytest.raises(ValueError, match="Invalid"):
        await svc.authenticate("UGR/9999/99", "anything")


# ── must_change_password flag is surfaced ───────────────────────


async def test_login_surfaces_must_change_password_flag(
    async_session, enrolled_student,
):
    user, _ = enrolled_student
    user.must_change_password = True
    await async_session.commit()

    svc = AuthService(async_session)
    _, must_change = await svc.authenticate("UGR/0001/14", "portal-pwd")
    assert must_change is True


# ── Deactivated account ─────────────────────────────────────────


async def test_deactivated_user_cannot_login(
    async_session, enrolled_student,
):
    user, _ = enrolled_student
    user.is_active = False
    await async_session.commit()

    svc = AuthService(async_session)
    with pytest.raises(ValueError, match="deactivated"):
        await svc.authenticate("UGR/0001/14", "portal-pwd")
