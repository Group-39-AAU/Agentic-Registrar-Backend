from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from typing import Protocol

from ..config.settings import get_settings


class StorageClient(ABC):
    """Abstract storage client for object storage backends."""

    @abstractmethod
    def build_upload_path(self, *, owner_type: str, owner_id: str, filename: str) -> str:
        """Return a storage path for a new upload."""

    @abstractmethod
    def build_upload_url(self, storage_path: str) -> str:
        """Return a URL (or pseudo-URL) that a client could upload to."""


class MockS3StorageClient(StorageClient):
    """Mock MinIO/S3-compatible storage client.

    This does not perform real uploads; it only constructs deterministic paths and URLs.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._endpoint = settings.minio_endpoint
        self._bucket = settings.minio_bucket_documents

    def build_upload_path(self, *, owner_type: str, owner_id: str, filename: str) -> str:
        suffix = uuid.uuid4()
        return f"{owner_type}/{owner_id}/{suffix}/{filename}"

    def build_upload_url(self, storage_path: str) -> str:
        return f"http://{self._endpoint}/{self._bucket}/{storage_path}"


def get_storage_client() -> StorageClient:
    """Factory for the default storage client."""

    return MockS3StorageClient()

