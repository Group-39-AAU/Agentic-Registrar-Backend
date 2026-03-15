from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class UploadStatus(str, StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class VerificationStatus(str, StrEnum):
    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"


class DocumentOut(BaseModel):
    id: str
    owner_type: str
    owner_id: str
    document_type: str
    original_filename: str
    content_type: str
    file_size: int
    checksum: str | None = None
    storage_path: str
    upload_status: UploadStatus
    verification_status: VerificationStatus


class InitiateUploadRequest(BaseModel):
    owner_type: str
    owner_id: str
    document_type: str
    original_filename: str
    content_type: str
    file_size: int = Field(gt=0)


class InitiateUploadResponse(BaseModel):
    document: DocumentOut
    upload_url: str


class CompleteUploadRequest(BaseModel):
    document_id: str
    checksum: str | None = None

