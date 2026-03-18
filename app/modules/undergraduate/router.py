"""
Undergraduate Admission module — FastAPI router.

Maps domain exceptions to HTTP responses.
Uses JWT auth via get_current_user dependency.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.database.session import get_db
from app.modules.auth.models import User
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
    PaymentCallbackRequest,
    PaymentInitiateResponse,
    StatusHistoryResponse,
)
from app.modules.undergraduate.service import ApplicationService, DecisionService
from app.shared.enums import ApplicationStatus, UserRole

router = APIRouter(prefix="/undergraduate", tags=["Undergraduate Admission"])


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
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Student submits a new undergraduate application."""
    svc = ApplicationService(db)
    try:
        app = await svc.submit_application(data, actor_id=current_user.id)
    except (DuplicateApplicationError, EntityNotFoundError) as e:
        _handle_domain_error(e)
    return app


@router.get("/applications", response_model=ApplicationListResponse)
async def list_applications(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all applications (registrar/admin view)."""
    svc = ApplicationService(db)
    items, total = await svc.list_applications(limit=limit, offset=offset)
    return ApplicationListResponse(items=items, total=total)


@router.get("/applications/me", response_model=list[ApplicationResponse])
async def list_my_applications(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List the current student's applications."""
    svc = ApplicationService(db)
    return await svc.list_my_applications(current_user.id)


@router.get("/applications/review-queue", response_model=list[ApplicationResponse])
async def review_queue(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get applications waiting for registrar review."""
    svc = ApplicationService(db)
    return await svc.get_review_queue(limit=limit, offset=offset)


@router.get("/applications/{application_id}", response_model=ApplicationResponse)
async def get_application(
    application_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
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
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Transition an application's status (registrar/system)."""
    svc = ApplicationService(db)
    try:
        return await svc.change_status(
            application_id, data,
            actor_id=current_user.id,
            actor_role=current_user.role,
        )
    except (EntityNotFoundError, InvalidStateTransitionError) as e:
        _handle_domain_error(e)


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
    Generate a simulated payment link for a self-sponsored application.
    Government-sponsored applications do not require payment.
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
):
    """
    Simulated payment gateway callback.
    In production this would be called by the payment provider.
    For now it's a manual trigger to simulate payment completion.
    """
    svc = ApplicationService(db)
    try:
        return await svc.complete_payment(application_id, data.payment_reference)
    except (EntityNotFoundError, InvalidStateTransitionError, MissingPrerequisiteError) as e:
        _handle_domain_error(e)


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


# ══════════════════════════════════════════════════════════════
#  Intake Agent Endpoint
# ══════════════════════════════════════════════════════════════


@router.post(
    "/applications/{application_id}/validate",
    response_model=ApplicationResponse,
    tags=["AI Agents"],
)
async def validate_application(
    application_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger the Intake Validation Agent on an application.

    The agent checks:
    1. Profile completeness (sponsorship, stream, program choices)
    2. Required documents uploaded (GRADE_12_CERTIFICATE, ID_CARD)
    3. Payment verified

    If all pass → transitions to UNDER_VERIFICATION.
    If any fail → writes an AIEvaluation with FLAG_FOR_REVIEW.
    """
    from app.ai.agents.intake_agent import AGENT_VERSION, run_intake_validation
    from app.ai.models import AIEvaluation, AIExecutionTrace

    svc = ApplicationService(db)

    try:
        application = await svc.get_application(application_id)
    except EntityNotFoundError as e:
        _handle_domain_error(e)

    # Run the LangGraph agent
    result = await run_intake_validation(
        application_id=application.id,
        sponsorship_type=application.sponsorship_type.value,
        stream=application.stream.value,
        admission_number=application.admission_number,
        program_choice_1_id=application.program_choice_1_id,
        program_choice_2_id=application.program_choice_2_id,
        program_choice_3_id=application.program_choice_3_id,
        payment_status=application.payment_status.value,
        current_status=application.current_status.value,
    )

    # Determine the recommendation
    from app.shared.enums import DecisionType as DT
    overall_result = result["overall_result"]
    checks_passed = result["checks_passed"]
    checks_failed = result["checks_failed"]
    traces_list = result["traces"]

    if overall_result == "PASS":
        recommended = DT.RECOMMEND_ADMIT
        confidence = 1.0
    else:
        recommended = DT.FLAG_FOR_REVIEW
        confidence = 0.0

    # Write AIEvaluation
    evaluation = AIEvaluation(
        application_id=application.id,
        agent_version=AGENT_VERSION,
        recommended_decision=recommended,
        confidence_score=confidence,
        is_overridden=False,
        summary_reasoning=(
            f"Intake validation: {overall_result}. "
            f"Passed: {checks_passed}. Failed: {checks_failed}."
        ),
    )
    db.add(evaluation)
    await db.flush()

    # Write AIExecutionTrace for each step
    for trace in traces_list:
        trace_entry = AIExecutionTrace(
            evaluation_id=evaluation.id,
            step_name=trace["step_name"],
            reasoning_log=trace["reasoning_log"],
        )
        db.add(trace_entry)

    # If all checks passed, transition to UNDER_VERIFICATION
    if overall_result == "PASS":
        try:
            from app.modules.undergraduate.schemas import ApplicationStatusUpdate
            await svc.change_status(
                application.id,
                ApplicationStatusUpdate(
                    new_status=ApplicationStatus.UNDER_VERIFICATION,
                    trigger_reason="Intake Agent: all completeness checks passed",
                ),
                actor_id=current_user.id,
                actor_role=UserRole.AGENT,
            )
        except InvalidStateTransitionError as e:
            _handle_domain_error(e)

    await db.commit()
    await db.refresh(application)
    return application


# ══════════════════════════════════════════════════════════════
#  Credential Verification Endpoint (Direct MoE Lookup)
# ══════════════════════════════════════════════════════════════


@router.post(
    "/applications/{application_id}/verify-credentials",
    response_model=ApplicationResponse,
    tags=["AI Agents"],
)
async def verify_credentials(
    application_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Verify academic credentials by looking up the student's admission number
    in the Ministry of Education (MoE) database.

    Cross-checks:
    1. Admission number exists in MoE database
    2. Student name (from User profile) matches MoE record

    If both match → PASS → status advances to AI_PRE_SCREENING.
    If not found or name mismatch → FLAG → status advances to FLAGGED_FOR_REVIEW.
    """
    from sqlalchemy import select

    from app.ai.models import AIEvaluation, AIExecutionTrace
    from app.modules.auth.models import User as UserModel
    from app.modules.moe.models import MoeStudentRecord

    AGENT_VERSION = "credential-verification-v2.0"

    svc = ApplicationService(db)

    try:
        application = await svc.get_application(application_id)
    except EntityNotFoundError as e:
        _handle_domain_error(e)

    admission_number = application.admission_number
    if not admission_number:
        raise HTTPException(400, "Application has no admission number")

    # Get the student's name from the User profile
    user_result = await db.execute(
        select(UserModel).where(UserModel.id == application.applicant_id)
    )
    applicant = user_result.scalar_one_or_none()
    if applicant is None:
        raise HTTPException(404, "Applicant user not found")

    student_name = f"{applicant.first_name} {applicant.last_name}".strip().upper()

    # Query MoE database
    moe_result = await db.execute(
        select(MoeStudentRecord).where(
            MoeStudentRecord.admission_number == admission_number
        )
    )
    moe_record = moe_result.scalar_one_or_none()

    # Cross-check logic
    issues = []
    traces = []

    if moe_record is None:
        issues.append(f"No MoE record found for admission number: {admission_number}")
        traces.append({
            "step_name": "moe_lookup",
            "reasoning_log": f"FAIL: No record found for admission_number={admission_number}",
        })
    else:
        traces.append({
            "step_name": "moe_lookup",
            "reasoning_log": f"OK: Found MoE record for {admission_number}: {moe_record.full_name}",
        })

        # Name cross-check
        moe_name = moe_record.full_name.strip().upper()
        if student_name in moe_name or moe_name in student_name:
            traces.append({
                "step_name": "name_cross_check",
                "reasoning_log": f"OK: Student name '{student_name}' matches MoE name '{moe_name}'",
            })
        else:
            issues.append(
                f"Name mismatch: application has '{student_name}' but MoE has '{moe_name}'"
            )
            traces.append({
                "step_name": "name_cross_check",
                "reasoning_log": f"FAIL: '{student_name}' does not match '{moe_name}'",
            })

    # Determine result
    from app.shared.enums import DecisionType as DT

    if not issues:
        overall_result = "PASS"
        confidence = 1.0
        recommended = DT.RECOMMEND_ADMIT
        summary = f"Credentials verified: admission number {admission_number} matches MoE record."
    else:
        overall_result = "FLAG"
        confidence = 0.0
        recommended = DT.FLAG_FOR_REVIEW
        summary = f"Credential issues: {'; '.join(issues)}"

    # Write AIEvaluation
    evaluation = AIEvaluation(
        application_id=application.id,
        agent_version=AGENT_VERSION,
        recommended_decision=recommended,
        confidence_score=confidence,
        is_overridden=False,
        summary_reasoning=summary,
    )
    db.add(evaluation)
    await db.flush()

    # Write AIExecutionTrace for each step
    for trace in traces:
        trace_entry = AIExecutionTrace(
            evaluation_id=evaluation.id,
            step_name=trace["step_name"],
            reasoning_log=trace["reasoning_log"],
        )
        db.add(trace_entry)

    # Transition application status
    try:
        from app.modules.undergraduate.schemas import ApplicationStatusUpdate as StatusUpd

        if overall_result == "PASS":
            new_status = ApplicationStatus.AI_PRE_SCREENING
            reason = f"Credential Verification: PASS — {summary}"
        else:
            new_status = ApplicationStatus.FLAGGED_FOR_REVIEW
            reason = f"Credential Verification: FLAGGED — {summary}"

        await svc.change_status(
            application.id,
            StatusUpd(new_status=new_status, trigger_reason=reason),
            actor_id=current_user.id,
            actor_role=UserRole.AGENT,
        )

        # If PASS: auto-transition to UAT_PENDING and generate UAT record
        if overall_result == "PASS":
            import random
            from datetime import datetime

            from app.modules.testing_center.models import UATRecord

            # Transition AI_PRE_SCREENING → UAT_PENDING
            await svc.change_status(
                application.id,
                StatusUpd(
                    new_status=ApplicationStatus.UAT_PENDING,
                    trigger_reason="Credentials verified — UAT scheduling initiated",
                ),
                actor_id=current_user.id,
                actor_role=UserRole.SYSTEM,
            )

            # Generate unique UAT ID
            year = datetime.now().year
            random_digits = random.randint(100000, 999999)
            uat_id = f"UAT-{year}-{random_digits}"

            # Create UAT record
            uat_record = UATRecord(
                uat_id=uat_id,
                application_id=application.id,
                student_name=student_name,
            )
            db.add(uat_record)

    except InvalidStateTransitionError as e:
        _handle_domain_error(e)

    await db.commit()
    await db.refresh(application)
    return application
