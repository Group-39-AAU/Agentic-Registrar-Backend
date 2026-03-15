from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from shared_kernel.security import PBKDF2PasswordHasher

from app.main import create_app
from app.domain.models import User
from app.infrastructure.db import get_db_session


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
def client(app):
    return TestClient(app)


def test_health_endpoint(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["details"]["service"] == "identity-service"


def test_login_failure(client: TestClient) -> None:
    response = client.post("/auth/login", json={"username": "unknown", "password": "bad"})
    assert response.status_code == 401


def test_login_and_introspect_flow(monkeypatch, app, client: TestClient) -> None:
    # Use an in-memory SQLite DB for this test by patching get_db_session
    from test_utils.db import create_test_session_factory

    SessionFactory = create_test_session_factory()

    def override_get_db_session():
        session: Session = SessionFactory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = override_get_db_session

    hasher = PBKDF2PasswordHasher()
    password = "secret123"
    hashed = hasher.hash(password)

    with SessionFactory() as session:
        user = User(
            username="testuser",
            email="test@example.com",
            hashed_password=hashed,
            is_active=True,
        )
        session.add(user)
        session.commit()

    # Login
    resp = client.post("/auth/login", json={"username": "testuser", "password": password})
    assert resp.status_code == 200
    token_data = resp.json()["data"]
    assert "access_token" in token_data
    token = token_data["access_token"]

    # Introspect
    introspect_resp = client.post("/auth/token/introspect", json={"token": token})
    assert introspect_resp.status_code == 200
    body = introspect_resp.json()
    assert body["active"] is True
    assert body["sub"] is not None

    # /auth/me
    me_resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_resp.status_code == 200
    me_body = me_resp.json()
    assert me_body["id"] == body["sub"]

