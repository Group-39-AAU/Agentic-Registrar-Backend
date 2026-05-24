"""
Undergraduate Admission module — FastAPI router.

Maps domain exceptions to HTTP responses.
Uses JWT auth via get_current_user dependency.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_email_service
from app.core.logging import get_logger
from app.database.session import get_db
from app.modules.auth.models import User
from app.modules.undergraduate.exceptions import (
    DuplicateApplicationError,
    EntityNotFoundError,
    InvalidStateTransitionError,
    MissingPrerequisiteError,
    UnauthorizedApplicationAccessError,
    UnauthorizedDecisionError,
)
from app.modules.undergraduate.schemas import (
    AdmissionTermCreate,
    AdmissionTermResponse,
    ApplicationCreate,
    ApplicationExistsResponse,
    ApplicationListResponse,
    ApplicationResponse,
    ApplicationStatusUpdate,
    CorrectionUpdateRequest,
    DecisionCreate,
    DecisionResponse,
    DocumentCreate,
    DocumentResponse,
    DocumentVerify,
    FlagContextResponse,
    FlagResolutionRequest,
    PaymentCallbackRequest,
    PaymentInitiateResponse,
    ReRunChecksResponse,
    StatusHistoryResponse,
)
from app.modules.undergraduate.service import ApplicationService, DecisionService
from app.shared.email import EmailService
from app.shared.enums import UserRole

router = APIRouter(prefix="/undergraduate", tags=["Undergraduate Admission"])

logger = get_logger("undergraduate.router")


@router.post("/admission-terms", response_model=AdmissionTermResponse, status_code=201)
async def create_admission_term(
    data: AdmissionTermCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create an undergraduate admission term."""
    if current_user.role not in {UserRole.REGISTRAR_OFFICER, UserRole.ADMIN}:
        raise HTTPException(403, "Only registrar officers or admins can create admission terms")
    svc = ApplicationService(db)
    return await svc.create_admission_term(data)


@router.get("/admission-terms/open", response_model=list[AdmissionTermResponse])
async def list_open_admission_terms(
    db: AsyncSession = Depends(get_db),
):
    """List all currently open undergraduate admission terms."""
    svc = ApplicationService(db)
    return await svc.list_open_admission_terms()


@router.get("/admission-terms", response_model=list[AdmissionTermResponse])
async def list_admission_terms(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    List every admission term (newest-first), regardless of ``is_open``.
    Used by the officer terms-management page.
    """
    if current_user.role not in {UserRole.REGISTRAR_OFFICER, UserRole.ADMIN}:
        raise HTTPException(403, "Only registrar officers or admins can browse all admission terms")
    svc = ApplicationService(db)
    return await svc.list_admission_terms()


@router.post(
    "/admission-terms/{term_id}/open",
    response_model=AdmissionTermResponse,
)
async def open_admission_term(
    term_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Set ``is_open=true`` on an admission term (idempotent)."""
    if current_user.role not in {UserRole.REGISTRAR_OFFICER, UserRole.ADMIN}:
        raise HTTPException(403, "Only registrar officers or admins can open admission terms")
    svc = ApplicationService(db)
    try:
        return await svc.set_admission_term_open(term_id, True)
    except EntityNotFoundError as e:
        raise HTTPException(404, str(e))


@router.post(
    "/admission-terms/{term_id}/close",
    response_model=AdmissionTermResponse,
)
async def close_admission_term(
    term_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Set ``is_open=false`` on an admission term (idempotent)."""
    if current_user.role not in {UserRole.REGISTRAR_OFFICER, UserRole.ADMIN}:
        raise HTTPException(403, "Only registrar officers or admins can close admission terms")
    svc = ApplicationService(db)
    try:
        return await svc.set_admission_term_open(term_id, False)
    except EntityNotFoundError as e:
        raise HTTPException(404, str(e))


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
    if isinstance(e, UnauthorizedApplicationAccessError):
        raise HTTPException(status_code=403, detail=str(e))
    if isinstance(e, MissingPrerequisiteError):
        raise HTTPException(status_code=400, detail=str(e))
    raise e


def _officer_admin_only(current_user: User) -> None:
    if current_user.role not in {UserRole.REGISTRAR_OFFICER, UserRole.ADMIN}:
        raise HTTPException(403, "Only registrar officers or admins can perform this action")


# ══════════════════════════════════════════════════════════════
#  Application Endpoints
# ══════════════════════════════════════════════════════════════


@router.post("/applications", response_model=ApplicationResponse, status_code=201)
async def submit_application(
    data: ApplicationCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Student submits a new undergraduate application."""
    svc = ApplicationService(db)
    try:
        app = await svc.submit_application(data, actor_id=current_user.id)
    except (DuplicateApplicationError, EntityNotFoundError) as e:
        _handle_domain_error(e)
    return await svc.to_application_response(app)


@router.get("/applications", response_model=ApplicationListResponse)
async def list_applications(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    term_id: uuid.UUID = Query(..., description="Admission term ID to filter by"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all applications (registrar/admin view)."""
    _officer_admin_only(current_user)
    svc = ApplicationService(db)
    items, total = await svc.list_applications(limit=limit, offset=offset, term_id=term_id)
    return ApplicationListResponse(items=items, total=total)


@router.get("/applications/me", response_model=list[ApplicationResponse])
async def list_my_applications(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List the current student's applications."""
    svc = ApplicationService(db)
    return await svc.list_my_applications(current_user.id)


@router.get("/applications/me/exists", response_model=ApplicationExistsResponse)
async def check_my_application_exists(
    admission_term_id: uuid.UUID = Query(..., description="Admission term ID to check"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Check whether the current applicant already has an application for an admission term."""
    svc = ApplicationService(db)
    exists = await svc.has_application_for_term(
        applicant_id=current_user.id,
        admission_term_id=admission_term_id,
    )
    return ApplicationExistsResponse(
        admission_term_id=admission_term_id,
        has_existing_application=exists,
    )


@router.get("/applications/review-queue", response_model=list[ApplicationResponse])
async def review_queue(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get applications waiting for registrar review."""
    _officer_admin_only(current_user)
    svc = ApplicationService(db)
    return await svc.get_review_queue(limit=limit, offset=offset)


@router.get("/applications/flagged-queue", response_model=list[ApplicationResponse])
async def flagged_queue(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get applications currently flagged for manual review."""
    _officer_admin_only(current_user)
    svc = ApplicationService(db)
    return await svc.get_flagged_queue(limit=limit, offset=offset)


@router.get("/applications/{application_id}", response_model=ApplicationResponse)
async def get_application(
    application_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific application by ID."""
    svc = ApplicationService(db)
    try:
        app = await svc.get_application(application_id)
    except EntityNotFoundError as e:
        _handle_domain_error(e)
    return await svc.to_application_response(app)


@router.patch("/applications/{application_id}/status", response_model=ApplicationResponse)
async def change_application_status(
    application_id: uuid.UUID,
    data: ApplicationStatusUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Transition an application's status (registrar/system)."""
    _officer_admin_only(current_user)
    svc = ApplicationService(db)
    try:
        app = await svc.change_status(
            application_id, data,
            actor_id=current_user.id,
            actor_role=current_user.role,
        )
    except (EntityNotFoundError, InvalidStateTransitionError) as e:
        _handle_domain_error(e)
    return await svc.to_application_response(app)


@router.patch(
    "/applications/{application_id}/submit-corrections",
    response_model=ApplicationResponse,
)
async def submit_corrections(
    application_id: uuid.UUID,
    data: CorrectionUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    email_service: EmailService = Depends(get_email_service),
):
    """Student submits admission-number/name corrections in CHANGES_REQUESTED."""
    svc = ApplicationService(db)
    try:
        app = await svc.update_requested_corrections(
            application_id=application_id,
            data=data,
            actor_id=current_user.id,
            email_service=email_service,
        )
    except (
        EntityNotFoundError,
        InvalidStateTransitionError,
        MissingPrerequisiteError,
        UnauthorizedApplicationAccessError,
    ) as e:
        _handle_domain_error(e)
    return await svc.to_application_response(app)


# ══════════════════════════════════════════════════════════════
#  Payment Endpoints (Simulated)
# ══════════════════════════════════════════════════════════════


@router.post(
    "/applications/{application_id}/payment/initiate",
    response_model=PaymentInitiateResponse,
)
async def initiate_payment(
    application_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Generate a simulated payment link for an application.
    """
    svc = ApplicationService(db)
    try:
        app = await svc.initiate_payment(application_id, actor_id=current_user.id)
    except (EntityNotFoundError, InvalidStateTransitionError, MissingPrerequisiteError) as e:
        _handle_domain_error(e)

    return PaymentInitiateResponse(
        application_id=app.id,
        payment_reference=app.payment_reference,
        payment_url=f"https://pay.registrar.example.com/checkout/{app.payment_reference}",
    )


@router.post(
    "/applications/{application_id}/payment/callback",
    response_model=ApplicationResponse,
)
async def payment_callback(
    application_id: uuid.UUID,
    data: PaymentCallbackRequest,
    db: AsyncSession = Depends(get_db),
    email_service: EmailService = Depends(get_email_service),
):
    """
    Simulated payment gateway callback.
    In production this would be called by the payment provider.
    For now it's a manual trigger to simulate payment completion.
    """
    svc = ApplicationService(db)
    try:
        app = await svc.complete_payment(
            application_id,
            data.payment_reference,
            email_service=email_service,
        )
    except (EntityNotFoundError, InvalidStateTransitionError, MissingPrerequisiteError) as e:
        _handle_domain_error(e)
    return await svc.to_application_response(app)


# ══════════════════════════════════════════════════════════════
#  Document Endpoints
# ══════════════════════════════════════════════════════════════


@router.post("/documents", response_model=DocumentResponse, status_code=201)
async def add_document(
    data: DocumentCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Attach a document to an application."""
    svc = ApplicationService(db)
    try:
        return await svc.add_document(data, actor_id=current_user.id)
    except EntityNotFoundError as e:
        _handle_domain_error(e)


@router.patch("/documents/{document_id}/verify", response_model=DocumentResponse)
async def verify_document(
    document_id: uuid.UUID,
    data: DocumentVerify,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Officer verifies or rejects a submitted document."""
    _officer_admin_only(current_user)
    svc = ApplicationService(db)
    try:
        return await svc.verify_document(
            document_id, data,
            actor_id=current_user.id,
            actor_role=current_user.role,
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
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the full status transition history for an application."""
    _officer_admin_only(current_user)
    svc = ApplicationService(db)
    return await svc.get_status_history(application_id)


@router.get(
    "/applications/{application_id}/flag-context",
    response_model=FlagContextResponse,
)
async def get_flag_context(
    application_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the latest AI context and traces for a flagged case."""
    _officer_admin_only(current_user)
    svc = ApplicationService(db)
    try:
        return await svc.get_flag_context(application_id)
    except EntityNotFoundError as e:
        _handle_domain_error(e)


@router.post(
    "/applications/{application_id}/resolve-flag",
    response_model=ApplicationResponse,
)
async def resolve_flag(
    application_id: uuid.UUID,
    data: FlagResolutionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Resolve a flagged case and route it back into the pipeline."""
    _officer_admin_only(current_user)
    svc = ApplicationService(db)
    try:
        app = await svc.resolve_flagged_application(
            application_id=application_id,
            data=data,
            actor_id=current_user.id,
            actor_role=current_user.role,
        )
    except (EntityNotFoundError, InvalidStateTransitionError, MissingPrerequisiteError) as e:
        _handle_domain_error(e)
    return await svc.to_application_response(app)


@router.post(
    "/applications/{application_id}/re-run-checks",
    response_model=ReRunChecksResponse,
)
async def rerun_checks(
    application_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    email_service: EmailService = Depends(get_email_service),
):
    """Re-run credential checks after student corrections."""
    _officer_admin_only(current_user)
    svc = ApplicationService(db)
    try:
        app = await svc.rerun_post_payment_checks(
            application_id=application_id,
            actor_id=current_user.id,
            actor_role=current_user.role,
            email_service=email_service,
        )
    except (EntityNotFoundError, InvalidStateTransitionError) as e:
        _handle_domain_error(e)
    return ReRunChecksResponse(
        application=await svc.to_application_response(app),
        message="Post-payment checks re-run completed",
    )


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
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Registrar officer records a final decision."""
    svc = DecisionService(db)
    try:
        return await svc.record_decision(
            application_id, data,
            actor_id=current_user.id,
            actor_role=current_user.role,
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
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the decision for an application (if one exists)."""
    svc = DecisionService(db)
    decision = await svc.get_decision(application_id)
    if decision is None:
        raise HTTPException(404, "No decision recorded for this application")
    return decision


