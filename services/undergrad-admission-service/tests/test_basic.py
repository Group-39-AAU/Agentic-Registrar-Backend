from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import create_app
from app.infrastructure.db.session import get_db_session


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
    assert data["details"]["service"] == "undergrad-admission-service"


def test_create_applicant_and_application(app, client: TestClient) -> None:
    SessionFactory = _override_db(app)

    resp = client.post(
        "/applications/applicants",
        json={
            "full_name": "Test Applicant",
            "email": "test@applicant.com",
            "phone_number": "123456789",
            "national_id": "NAT-1",
        },
    )
    assert resp.status_code == 200
    applicant = resp.json()["data"]
    applicant_id = applicant["id"]

    resp2 = client.post(
        "/applications",
        json={
            "applicant_id": applicant_id,
            "program_code": "CS",
            "intake_year": 2026,
        },
    )
    assert resp2.status_code == 200
    app_data = resp2.json()["data"]
    assert app_data["applicant_id"] == applicant_id

