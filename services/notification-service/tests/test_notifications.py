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
    assert data["details"]["service"] == "notification-service"


def test_send_and_get_notification(app, client: TestClient) -> None:
    _ = _override_db(app)

    body = {
        "channel": "email",
        "recipient": "user@example.com",
        "subject": "Test",
        "body": "Hello",
        "template_key": "welcome",
    }
    resp = client.post("/notifications/send", json=body)
    assert resp.status_code == 200
    data = resp.json()["data"]
    notif_id = data["id"]
    assert data["channel"] == "email"
    assert data["recipient"] == "user@example.com"

    resp2 = client.get(f"/notifications/{notif_id}")
    assert resp2.status_code == 200
    fetched = resp2.json()["data"]
    assert fetched["id"] == notif_id

