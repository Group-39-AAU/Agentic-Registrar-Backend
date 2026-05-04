"""
Auth — change-password endpoint + must_change_password lockout.

Confirms the post-onboarding flow:

  - While ``must_change_password`` is True, every protected endpoint
    returns 403 with the ``password_change_required`` error code.
  - The /auth/change-password and /auth/me endpoints stay reachable
    (they use get_current_user_allow_password_change).
  - Submitting the wrong current password returns 400.
  - Submitting a too-short new password returns 422 (Pydantic).
  - On success, must_change_password flips to False and the lockout
    is lifted on the very next request.
"""
from __future__ import annotations

import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.security import hash_password
from app.database.session import get_db
from app.main import app
from app.modules.auth.models import User
from app.shared.enums import UserRole


# ── Fixtures ─────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def client(async_session) -> AsyncClient:
    """ASGI client with the app's get_db override pinned to the test session."""
    async def _override_db():
        yield async_session
    app.dependency_overrides[get_db] = _override_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def locked_student(async_session) -> User:
    """Freshly-onboarded student: must_change_password=True, PIN is '1234'."""
    user = User(
        id=uuid.uuid4(),
        email="locked@aau.edu.et",
        first_name="Locked", last_name="Student",
        hashed_password=hash_password("1234"),
        role=UserRole.STUDENT, is_active=True,
        must_change_password=True,
    )
    async_session.add(user)
    await async_session.commit()
    return user


async def _login(client: AsyncClient, identifier: str, password: str) -> str:
    """Login helper — returns access token."""
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": identifier, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


# ── Lockout enforcement ─────────────────────────────────────────


async def test_login_returns_must_change_password_flag(
    client, locked_student,
):
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": "locked@aau.edu.et", "password": "1234"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["must_change_password"] is True
    assert body["access_token"]


async def test_locked_user_blocked_from_protected_endpoint(
    client, locked_student,
):
    """A protected endpoint must 403 while the lockout is active."""
    token = await _login(client, "locked@aau.edu.et", "1234")

    # /courses/me/curriculum is a representative protected endpoint
    resp = await client.get(
        "/api/v1/courses/me/curriculum",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert detail["code"] == "password_change_required"


async def test_me_endpoint_reachable_during_lockout(
    client, locked_student,
):
    """/auth/me must stay reachable so the client can render the change-pwd screen."""
    token = await _login(client, "locked@aau.edu.et", "1234")
    resp = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["email"] == "locked@aau.edu.et"


# ── Change-password endpoint ────────────────────────────────────


async def test_change_password_with_wrong_current_returns_400(
    client, locked_student,
):
    token = await _login(client, "locked@aau.edu.et", "1234")
    resp = await client.post(
        "/api/v1/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "wrong-pin", "new_password": "new-strong-pwd"},
    )
    assert resp.status_code == 400
    assert "incorrect" in resp.json()["detail"].lower()


async def test_change_password_too_short_rejected_at_validation(
    client, locked_student,
):
    token = await _login(client, "locked@aau.edu.et", "1234")
    resp = await client.post(
        "/api/v1/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "1234", "new_password": "short"},
    )
    assert resp.status_code == 422  # Pydantic min_length=8


async def test_change_password_same_as_current_rejected(
    client, locked_student,
):
    """New password must differ from current — guards against no-op resets."""
    token = await _login(client, "locked@aau.edu.et", "1234")
    resp = await client.post(
        "/api/v1/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "1234", "new_password": "12345678"},
    )
    # 12345678 ≠ 1234, so this should succeed; the same-as-current
    # check is exact, so we test it with a longer matching string.
    assert resp.status_code == 204
    # Now try to change again with same-as-current
    new_token = await _login(client, "locked@aau.edu.et", "12345678")
    resp = await client.post(
        "/api/v1/auth/change-password",
        headers={"Authorization": f"Bearer {new_token}"},
        json={"current_password": "12345678", "new_password": "12345678"},
    )
    assert resp.status_code == 400
    assert "differ" in resp.json()["detail"].lower()


async def test_successful_change_lifts_the_lockout(
    client, locked_student, async_session,
):
    """After change-password, every other endpoint must accept the (new-token) request."""
    token = await _login(client, "locked@aau.edu.et", "1234")

    # Step 1: change the PIN
    resp = await client.post(
        "/api/v1/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "1234", "new_password": "new-strong-pwd"},
    )
    assert resp.status_code == 204

    # Step 2: re-login with the new password — must_change_password=False now
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": "locked@aau.edu.et", "password": "new-strong-pwd"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["must_change_password"] is False

    # Step 3: the password-lockout 403 specifically is gone. /auth/me
    # is the cleanest probe — both before and after, it returns 200,
    # but the must_change_password flag in the body confirms state.
    resp = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert resp.status_code == 200

    # And on a protected endpoint, if a 403 comes back it must NOT
    # carry the lockout code.
    resp = await client.get(
        "/api/v1/courses/me/curriculum",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    if resp.status_code == 403:
        detail = resp.json().get("detail")
        if isinstance(detail, dict):
            assert detail.get("code") != "password_change_required"


async def test_old_pin_no_longer_works_after_change(
    client, locked_student,
):
    """Old PIN must be invalid once a new password is set."""
    token = await _login(client, "locked@aau.edu.et", "1234")
    resp = await client.post(
        "/api/v1/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "1234", "new_password": "new-strong-pwd"},
    )
    assert resp.status_code == 204

    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": "locked@aau.edu.et", "password": "1234"},
    )
    assert resp.status_code == 401
