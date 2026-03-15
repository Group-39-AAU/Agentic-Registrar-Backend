from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import create_app
from app.domain.models import Document, UploadStatus, VerificationStatus
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
    assert data["details"]["service"] == "document-service"


def test_initiate_and_complete_upload_flow(app, client: TestClient) -> None:
    SessionFactory = _override_db(app)

    body = {
        "owner_type": "user",
        "owner_id": "user-1",
        "document_type": "passport",
        "original_filename": "doc.pdf",
        "content_type": "application/pdf",
        "file_size": 1234,
    }
    resp = client.post("/documents/initiate-upload", json=body)
    assert resp.status_code == 200
    payload = resp.json()["data"]
    document = payload["document"]
    document_id = document["id"]
    assert payload["upload_url"]
    assert document["upload_status"] == "pending"

    # Complete upload
    resp2 = client.post(
        "/documents/complete-upload",
        json={"document_id": document_id, "checksum": "abc123"},
    )
    assert resp2.status_code == 200
    completed = resp2.json()["data"]
    assert completed["upload_status"] == "completed"
    assert completed["checksum"] == "abc123"

    # Fetch
    resp3 = client.get(f"/documents/{document_id}")
    assert resp3.status_code == 200
    fetched = resp3.json()["data"]
    assert fetched["id"] == document_id

