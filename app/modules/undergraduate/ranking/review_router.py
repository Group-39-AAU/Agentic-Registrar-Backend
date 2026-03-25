"""
Officer Review router — Human-in-the-loop admission decisions.

Provides endpoints for reviewing ranked students and issuing
final admission decisions (ADMIT / REJECT), either one at a time
or in batch.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.database.session import get_db
from app.modules.auth.models import User
from app.modules.moe.models import MoeStudentRecord
from app.modules.programs.models import AcademicProgram
from app.modules.undergraduate.ranking.models import RankingResult
from app.modules.undergraduate.ranking.schemas import (
    BatchDecisionRequest,
    BatchDecisionResponse,
    StudentReviewCard,
    StudentReviewListResponse,
)
from app.modules.testing_center.models import UATRecord
from app.modules.undergraduate.models import (
    RegistrarDecision,
    UndergraduateApplication,
)
from app.modules.undergraduate.service import ApplicationService
from app.modules.undergraduate.schemas import (
    ApplicationStatusUpdate,
    DecisionCreate,
    DecisionResponse,
)
from app.ai.models import AIEvaluation
from app.shared.enums import (
    ApplicationStatus,
    DecisionType,
    SponsorshipType,
    UserRole,
)

router = APIRouter(prefix="/undergraduate/review", tags=["Undergraduate Officer Review"])


# ── Helper ───────────────────────────────────────────────────

def _role_gate(user: User):
    if user.role not in {UserRole.REGISTRAR_OFFICER, UserRole.ADMIN}:
        raise HTTPException(403, "Only registrar officers or admins can access review")


async def _build_review_card(
    app: UndergraduateApplication,
    db: AsyncSession,
) -> StudentReviewCard:
    """Build a rich review card by joining data from multiple tables."""

    # Ranking result
    rr = (await db.execute(
        select(RankingResult).where(
            RankingResult.application_id == app.id,
        ).order_by(RankingResult.created_at.desc())
    )).scalars().first()

    # MoE record
    moe = (await db.execute(
        select(MoeStudentRecord).where(
            MoeStudentRecord.admission_number == app.admission_number
        )
    )).scalar_one_or_none()

    # UAT record
    uat = (await db.execute(
        select(UATRecord).where(
            UATRecord.application_id == app.id,
            UATRecord.is_completed == True,  # noqa: E712
        )
    )).scalars().first()

    # AI evaluation (latest)
    ai_eval = (await db.execute(
        select(AIEvaluation).where(
            AIEvaluation.application_id == app.id,
        ).order_by(AIEvaluation.created_at.desc())
    )).scalars().first()

    # Resolve program names for preferences
    prog_names = {}
    prog_ids = [
        app.program_choice_1_id,
        app.program_choice_2_id,
        app.program_choice_3_id,
    ]
    prog_ids_valid = [pid for pid in prog_ids if pid is not None]
    if prog_ids_valid:
        prog_result = await db.execute(
            select(AcademicProgram).where(AcademicProgram.id.in_(prog_ids_valid))
        )
        for p in prog_result.scalars().all():
            prog_names[p.id] = f"{p.code} — {p.name}"

    # Resolve assigned program name
    assigned_prog_name = None
    assigned_prog_code = None
    if rr and rr.assigned_program_id:
        ap = (await db.execute(
            select(AcademicProgram).where(AcademicProgram.id == rr.assigned_program_id)
        )).scalar_one_or_none()
        if ap:
            assigned_prog_name = ap.name
            assigned_prog_code = ap.code

    # Existing decision?
    has_decision = (await db.execute(
        select(func.count(RegistrarDecision.id)).where(
            RegistrarDecision.application_id == app.id
        )
    )).scalar() > 0

    # Student name from MoE or User table
    student_name = moe.full_name if moe else app.admission_number

    return StudentReviewCard(
        application_id=app.id,
        student_name=student_name,
        admission_number=app.admission_number,
        sponsorship_type=app.sponsorship_type.value,
        stream=app.stream.value,
        grade12_score=moe.total_score if moe else 0.0,
        uat_score=uat.score if uat else 0.0,
        final_score=rr.final_score if rr else 0.0,
        rank_position=rr.rank_position if rr else 0,
        assigned_program_name=assigned_prog_name,
        assigned_program_code=assigned_prog_code,
        assigned_stream=rr.assigned_stream.value if rr and rr.assigned_stream else None,
        is_assigned=rr.is_assigned if rr else False,
        assignment_detail=rr.assignment_detail if rr else None,
        program_choice_1=prog_names.get(app.program_choice_1_id),
        program_choice_2=prog_names.get(app.program_choice_2_id),
        program_choice_3=prog_names.get(app.program_choice_3_id),
        ai_recommended_decision=ai_eval.recommended_decision.value if ai_eval else None,
        ai_confidence=ai_eval.confidence_score if ai_eval else None,
        current_status=app.current_status.value,
        has_decision=has_decision,
    )


# ══════════════════════════════════════════════════════════════
#  GET /review/students — Paginated review list
# ══════════════════════════════════════════════════════════════

@router.get("/students", response_model=StudentReviewListResponse)
async def list_students_for_review(
    sponsorship_type: SponsorshipType = Query(
        ..., description="Filter by SELF_SPONSORED or GOVERNMENT"
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Paginated list of students awaiting officer review.
    Filtered by sponsorship type (self-sponsored or government).
    """
    _role_gate(current_user)

    base_filter = [
        UndergraduateApplication.current_status == ApplicationStatus.PENDING_REVIEW,
        UndergraduateApplication.is_deleted == False,  # noqa: E712
        UndergraduateApplication.sponsorship_type == sponsorship_type,
    ]

    # Total count
    total = (await db.execute(
        select(func.count(UndergraduateApplication.id)).where(*base_filter)
    )).scalar()

    # Paginated query
    offset = (page - 1) * page_size
    result = await db.execute(
        select(UndergraduateApplication)
        .where(*base_filter)
        .offset(offset)
        .limit(page_size)
    )
    applications = result.scalars().all()

    # Build review cards
    cards = []
    for app in applications:
        card = await _build_review_card(app, db)
        cards.append(card)

    return StudentReviewListResponse(
        items=cards,
        total=total,
        page=page,
        page_size=page_size,
    )


# ══════════════════════════════════════════════════════════════
#  GET /review/students/{application_id} — Single student detail
# ══════════════════════════════════════════════════════════════

@router.get("/students/{application_id}", response_model=StudentReviewCard)
async def get_student_review_detail(
    application_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the full review card for a specific student."""
    _role_gate(current_user)

    app = (await db.execute(
        select(UndergraduateApplication).where(
            UndergraduateApplication.id == application_id,
            UndergraduateApplication.is_deleted == False,  # noqa: E712
        )
    )).scalar_one_or_none()

    if not app:
        raise HTTPException(404, "Application not found")

    return await _build_review_card(app, db)


# ══════════════════════════════════════════════════════════════
#  POST /review/decide/{application_id} — Single decision
# ══════════════════════════════════════════════════════════════

@router.post("/decide/{application_id}", response_model=DecisionResponse)
async def decide_single(
    application_id: uuid.UUID,
    data: DecisionCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Record a final admission decision for a single student.
    Accepts ADMIT, REJECT, or WAITLIST.
    Transitions the application to DECIDED.
    """
    _role_gate(current_user)

    # Validate decision type
    if data.human_decision not in {DecisionType.ADMIT, DecisionType.REJECT, DecisionType.WAITLIST}:
        raise HTTPException(400, "Decision must be ADMIT, REJECT, or WAITLIST")

    # Fetch application
    app = (await db.execute(
        select(UndergraduateApplication).where(
            UndergraduateApplication.id == application_id,
            UndergraduateApplication.is_deleted == False,  # noqa: E712
        )
    )).scalar_one_or_none()

    if not app:
        raise HTTPException(404, "Application not found")

    if app.current_status != ApplicationStatus.PENDING_REVIEW:
        raise HTTPException(
            400,
            f"Application must be in PENDING_REVIEW status (currently: {app.current_status.value})"
        )

    # Check for existing decision
    existing = (await db.execute(
        select(RegistrarDecision).where(
            RegistrarDecision.application_id == application_id
        )
    )).scalar_one_or_none()

    if existing:
        raise HTTPException(409, "A decision has already been recorded for this application")

    # Create decision record
    decision = RegistrarDecision(
        application_id=application_id,
        reviewer_id=current_user.id,
        human_decision=data.human_decision,
        justification_remarks=data.justification_remarks,
        override_reason=data.override_reason,
    )
    db.add(decision)

    # Update final_decision on the application
    app.final_decision = data.human_decision.value

    # Transition to DECIDED
    svc = ApplicationService(db)
    await svc.change_status(
        application_id,
        ApplicationStatusUpdate(
            new_status=ApplicationStatus.DECIDED,
            trigger_reason=f"Officer decision: {data.human_decision.value} — {data.justification_remarks[:100]}",
        ),
        actor_id=current_user.id,
        actor_role=current_user.role,
    )

    await db.commit()
    await db.refresh(decision)
    return decision


# ══════════════════════════════════════════════════════════════
#  POST /review/decide/batch — Batch decisions
# ══════════════════════════════════════════════════════════════

@router.post("/decide/batch", response_model=BatchDecisionResponse)
async def decide_batch(
    data: BatchDecisionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Record admission decisions for multiple students at once.
    Each item in the batch is processed independently — failures
    in one item do not block others.
    """
    _role_gate(current_user)

    processed = 0
    failed = 0
    results = []

    svc = ApplicationService(db)

    for item in data.decisions:
        try:
            # Validate decision type
            decision_type = DecisionType(item.human_decision)
            if decision_type not in {DecisionType.ADMIT, DecisionType.REJECT, DecisionType.WAITLIST}:
                raise ValueError("Decision must be ADMIT, REJECT, or WAITLIST")

            # Fetch application
            app = (await db.execute(
                select(UndergraduateApplication).where(
                    UndergraduateApplication.id == item.application_id,
                    UndergraduateApplication.is_deleted == False,  # noqa: E712
                )
            )).scalar_one_or_none()

            if not app:
                raise ValueError("Application not found")

            if app.current_status != ApplicationStatus.PENDING_REVIEW:
                raise ValueError(f"Not in PENDING_REVIEW (currently: {app.current_status.value})")

            # Check existing decision
            existing = (await db.execute(
                select(RegistrarDecision).where(
                    RegistrarDecision.application_id == item.application_id
                )
            )).scalar_one_or_none()

            if existing:
                raise ValueError("Decision already exists")

            # Create decision
            decision = RegistrarDecision(
                application_id=item.application_id,
                reviewer_id=current_user.id,
                human_decision=decision_type,
                justification_remarks=item.justification_remarks,
            )
            db.add(decision)

            # Update final_decision
            app.final_decision = decision_type.value

            # Transition
            await svc.change_status(
                item.application_id,
                ApplicationStatusUpdate(
                    new_status=ApplicationStatus.DECIDED,
                    trigger_reason=f"Batch decision: {decision_type.value}",
                ),
                actor_id=current_user.id,
                actor_role=current_user.role,
            )

            processed += 1
            results.append({
                "application_id": str(item.application_id),
                "status": "OK",
                "decision": decision_type.value,
            })

        except Exception as e:
            failed += 1
            results.append({
                "application_id": str(item.application_id),
                "status": "FAILED",
                "error": str(e),
            })

    await db.commit()

    return BatchDecisionResponse(
        processed=processed,
        failed=failed,
        results=results,
    )
