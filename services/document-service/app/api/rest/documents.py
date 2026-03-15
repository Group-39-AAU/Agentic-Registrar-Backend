from __future__ import annotations

from http import HTTPStatus

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from contracts.responses import ResponseEnvelope
from shared_kernel.api import make_error_response
from shared_kernel.exceptions import NotFoundError

from ...application.services import DocumentService
from ...domain.schemas import (
    CompleteUploadRequest,
    DocumentOut,
    InitiateUploadRequest,
    InitiateUploadResponse,
)
from ...infrastructure.db import get_db_session
from ...infrastructure.storage import get_storage_client

router = APIRouter(prefix="/documents", tags=["documents"])


def get_document_service(session: Session = Depends(get_db_session)) -> DocumentService:
    storage = get_storage_client()
    return DocumentService(session=session, storage=storage)


@router.post("/initiate-upload", response_model=ResponseEnvelope[InitiateUploadResponse])
def initiate_upload(
    req: InitiateUploadRequest,
    service: DocumentService = Depends(get_document_service),
):
    result = service.initiate_upload(req)
    return ResponseEnvelope[InitiateUploadResponse](data=result)


@router.post("/complete-upload", response_model=ResponseEnvelope[DocumentOut])
def complete_upload(
    req: CompleteUploadRequest,
    service: DocumentService = Depends(get_document_service),
):
    try:
        doc = service.complete_upload(req)
    except NotFoundError as exc:
        error = make_error_response(
            HTTPStatus.NOT_FOUND,
            code="document_not_found",
            message=str(exc),
        )
        raise HTTPException(status_code=error.status, detail=error.error.message)
    return ResponseEnvelope[DocumentOut](data=doc)


@router.get("/{document_id}", response_model=ResponseEnvelope[DocumentOut])
def get_document(
    document_id: str,
    service: DocumentService = Depends(get_document_service),
):
    try:
        doc = service.get_document(document_id)
    except NotFoundError as exc:
        error = make_error_response(
            HTTPStatus.NOT_FOUND,
            code="document_not_found",
            message=str(exc),
        )
        raise HTTPException(status_code=error.status, detail=error.error.message)
    return ResponseEnvelope[DocumentOut](data=doc)

