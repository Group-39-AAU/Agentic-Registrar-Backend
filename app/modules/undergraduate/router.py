"""
Undergraduate Admission module — FastAPI router.

Maps domain exceptions to HTTP responses.
Uses temporary auth stubs until the auth module is fully implemented.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.modules.undergraduate.exceptions import (
    DuplicateApplicationError,
    EntityNotFoundError,
    InvalidStateTransitionError,
    MissingPrerequisiteError,
    UnauthorizedDecisionError,
)
from app.modules.undergraduate.schemas import (
    AIEvaluationResponse,
    ApplicationCreate,
    ApplicationListResponse,
    ApplicationResponse,
    ApplicationStatusUpdate,
    DecisionCreate,
    DecisionResponse,
    DocumentCreate,
    DocumentResponse,
    DocumentVerify,
    StatusHistoryResponse,
)
from app.modules.undergraduate.service import ApplicationService, DecisionService
from app.shared.enums import UserRole

router = APIRouter(prefix="/undergraduate", tags=["Undergraduate Admission"])


# ── Temporary Auth Stubs ─────────────────────────────────────
# These will be replaced by real auth dependencies (core/dependencies.py)
# once the auth module is fully implemented.

TEMP_STUDENT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
TEMP_OFFICER_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")


# ── Exception → HTTP mapping ─────────────────────────────────

def _handle_domain_error(e: Exception) -> None:
    """Maps domain exceptions to FastAPI HTTPExceptions."""
    if isinstance(e, EntityNotFoundError):
        raise HTTPException(status_code=404, detail=str(e))
    if isinstance(e, DuplicateApplicationError):
        raise HTTPException(status_code=409, detail=str(e))
    if isinstance(e, InvalidStateTransitionError):
        raise HTTPException(status_code=400, detail=str(e))
    if isinstance(e, UnauthorizedDecisionError):
        raise HTTPException(status_code=403, detail=str(e))
    if isinstance(e, MissingPrerequisiteError):
        raise HTTPException(status_code=400, detail=str(e))
    raise e


# ══════════════════════════════════════════════════════════════
#  Application Endpoints
# ══════════════════════════════════════════════════════════════


@router.post("/applications", response_model=ApplicationResponse, status_code=201)
async def submit_application(
    data: ApplicationCreate,
    db: AsyncSession = Depends(get_db),
):
    """Student submits a new undergraduate application."""
    svc = ApplicationService(db)
    try:
        app = await svc.submit_application(data, actor_id=TEMP_STUDENT_ID)
    except (DuplicateApplicationError, EntityNotFoundError) as e:
        _handle_domain_error(e)
    return app


@router.get("/applications", response_model=ApplicationListResponse)
async def list_applications(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List all applications (registrar/admin view)."""
    svc = ApplicationService(db)
    items, total = await svc.list_applications(limit=limit, offset=offset)
    return ApplicationListResponse(items=items, total=total)


@router.get("/applications/me", response_model=list[ApplicationResponse])
async def list_my_applications(
    db: AsyncSession = Depends(get_db),
):
    """List the current student's applications."""
    svc = ApplicationService(db)
    return await svc.list_my_applications(TEMP_STUDENT_ID)


@router.get("/applications/review-queue", response_model=list[ApplicationResponse])
async def review_queue(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Get applications waiting for registrar review."""
    svc = ApplicationService(db)
    return await svc.get_review_queue(limit=limit, offset=offset)


@router.get("/applications/{application_id}", response_model=ApplicationResponse)
async def get_application(
    application_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get a specific application by ID."""
    svc = ApplicationService(db)
    try:
        return await svc.get_application(application_id)
    except EntityNotFoundError as e:
        _handle_domain_error(e)


@router.patch("/applications/{application_id}/status", response_model=ApplicationResponse)
async def change_application_status(
    application_id: uuid.UUID,
    data: ApplicationStatusUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Transition an application's status (registrar/system)."""
    svc = ApplicationService(db)
    try:
        return await svc.change_status(
            application_id, data,
            actor_id=TEMP_OFFICER_ID,
            actor_role=UserRole.REGISTRAR_OFFICER,
        )
    except (EntityNotFoundError, InvalidStateTransitionError) as e:
        _handle_domain_error(e)


# ══════════════════════════════════════════════════════════════
#  Document Endpoints
# ══════════════════════════════════════════════════════════════


@router.post("/documents", response_model=DocumentResponse, status_code=201)
async def add_document(
    data: DocumentCreate,
    db: AsyncSession = Depends(get_db),
):
    """Attach a document to an application."""
    svc = ApplicationService(db)
    try:
        return await svc.add_document(data, actor_id=TEMP_STUDENT_ID)
    except EntityNotFoundError as e:
        _handle_domain_error(e)


@router.patch("/documents/{document_id}/verify", response_model=DocumentResponse)
async def verify_document(
    document_id: uuid.UUID,
    data: DocumentVerify,
    db: AsyncSession = Depends(get_db),
):
    """Officer verifies or rejects a submitted document."""
    svc = ApplicationService(db)
    try:
        return await svc.verify_document(
            document_id, data,
            actor_id=TEMP_OFFICER_ID,
            actor_role=UserRole.REGISTRAR_OFFICER,
        )
    except EntityNotFoundError as e:
        _handle_domain_error(e)


# ══════════════════════════════════════════════════════════════
#  Status History Endpoints
# ══════════════════════════════════════════════════════════════


@router.get(
    "/applications/{application_id}/history",
    response_model=list[StatusHistoryResponse],
)
async def get_status_history(
    application_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get the full status transition history for an application."""
    svc = ApplicationService(db)
    return await svc.get_status_history(application_id)


# ══════════════════════════════════════════════════════════════
#  Decision Endpoints
# ══════════════════════════════════════════════════════════════


@router.post(
    "/applications/{application_id}/decision",
    response_model=DecisionResponse,
    status_code=201,
)
async def record_decision(
    application_id: uuid.UUID,
    data: DecisionCreate,
    db: AsyncSession = Depends(get_db),
):
    """Registrar officer records a final decision."""
    svc = DecisionService(db)
    try:
        return await svc.record_decision(
            application_id, data,
            actor_id=TEMP_OFFICER_ID,
            actor_role=UserRole.REGISTRAR_OFFICER,
        )
    except (
        EntityNotFoundError,
        InvalidStateTransitionError,
        UnauthorizedDecisionError,
    ) as e:
        _handle_domain_error(e)


@router.get(
    "/applications/{application_id}/decision",
    response_model=DecisionResponse,
)
async def get_decision(
    application_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get the decision for an application (if one exists)."""
    svc = DecisionService(db)
    decision = await svc.get_decision(application_id)
    if decision is None:
        raise HTTPException(404, "No decision recorded for this application")
    return decision
