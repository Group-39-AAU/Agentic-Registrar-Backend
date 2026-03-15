from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import create_app
from app.infrastructure.db import get_db_session


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
def client(app):
    return TestClient(app)


def _override_db(app):
    from test_utils.db import create_test_session_factory

    SessionFactory = create_test_session_factory()

    def override_get_db_session():
        session: Session = SessionFactory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = override_get_db_session
    return SessionFactory


def test_health(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["details"]["service"] == "course-management-service"


def test_registration_lifecycle(app, client: TestClient) -> None:
    _ = _override_db(app)

    body = {
        "student_id": "student-1",
        "term": "2026-FALL",
        "courses": [["CS101", 3.0], ["MATH201", 4.0]],
    }
    resp = client.post("/registrations", json=body)
    assert resp.status_code == 200
    reg = resp.json()["data"]
    reg_id = reg["id"]

    resp2 = client.post(f"/registrations/{reg_id}/validate")
    assert resp2.status_code == 200

    resp3 = client.post(f"/registrations/{reg_id}/finalize")
    assert resp3.status_code == 200

