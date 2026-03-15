from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from ..domain.models import Document, UploadStatus, VerificationStatus
from ..domain.schemas import (
    CompleteUploadRequest,
    DocumentOut,
    InitiateUploadRequest,
    InitiateUploadResponse,
)
from ..infrastructure.repositories import DocumentRepository
from ..infrastructure.storage import StorageClient


class DocumentService:
    def __init__(self, session: Session, storage: StorageClient) -> None:
        self._session = session
        self._repo = DocumentRepository(session)
        self._storage = storage

    def initiate_upload(self, req: InitiateUploadRequest) -> InitiateUploadResponse:
        storage_path = self._storage.build_upload_path(
            owner_type=req.owner_type,
            owner_id=req.owner_id,
            filename=req.original_filename,
        )
        doc = Document(
            owner_type=req.owner_type,
            owner_id=req.owner_id,
            document_type=req.document_type,
            original_filename=req.original_filename,
            content_type=req.content_type,
            file_size=req.file_size,
            checksum=None,
            storage_path=storage_path,
            upload_status=UploadStatus.PENDING,
            verification_status=VerificationStatus.PENDING,
        )
        self._repo.add(doc)
        self._session.commit()
        upload_url = self._storage.build_upload_url(storage_path)
        return InitiateUploadResponse(document=self._to_out(doc), upload_url=upload_url)

    def complete_upload(self, req: CompleteUploadRequest) -> DocumentOut:
        doc_id = uuid.UUID(req.document_id)
        doc = self._repo.get(doc_id)
        doc.upload_status = UploadStatus.COMPLETED
        if req.checksum is not None:
            doc.checksum = req.checksum
        self._session.commit()
        return self._to_out(doc)

    def get_document(self, document_id: str) -> DocumentOut:
        doc = self._repo.get(uuid.UUID(document_id))
        return self._to_out(doc)

    @staticmethod
    def _to_out(doc: Document) -> DocumentOut:
        return DocumentOut(
            id=str(doc.id),
            owner_type=doc.owner_type,
            owner_id=doc.owner_id,
            document_type=doc.document_type,
            original_filename=doc.original_filename,
            content_type=doc.content_type,
            file_size=doc.file_size,
            checksum=doc.checksum,
            storage_path=doc.storage_path,
            upload_status=UploadStatus(doc.upload_status),
            verification_status=VerificationStatus(doc.verification_status),
        )

