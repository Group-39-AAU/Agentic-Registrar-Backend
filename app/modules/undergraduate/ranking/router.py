"""
Ranking module — FastAPI router.

Provides endpoints to trigger ranking, view results, and get summaries.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.database.session import get_db
from app.modules.auth.models import User
from app.modules.moe.models import MoeStudentRecord
from app.modules.programs.models import AcademicProgram
from app.modules.undergraduate.ranking.models import RankingResult, StreamQuota
from app.modules.undergraduate.ranking.service import RankingService
from app.modules.undergraduate.ranking.schemas import (
    ProgramCutoffResponse,
    RankingResultResponse,
    RankingRunResponse,
    RankingSummaryResponse,
    StreamCutoffResponse,
    StreamQuotaCreate,
    StreamQuotaResponse,
    StreamQuotaUpdate,
)
from app.modules.testing_center.models import UATRecord
from app.modules.undergraduate.models import UndergraduateAdmissionTerm, UndergraduateApplication
from app.modules.undergraduate.service import ApplicationService
from app.shared.enums import ApplicationStatus, StreamType, UserRole

router = APIRouter(prefix="/undergraduate/ranking", tags=["Undergraduate Eligibility & Ranking"])


# ══════════════════════════════════════════════════════════════
#  POST /ranking/run — Trigger a ranking batch
# ══════════════════════════════════════════════════════════════

@router.post("/run", response_model=RankingRunResponse)
async def run_ranking(
    admission_term_id: uuid.UUID = Query(..., description="Admission term ID to run ranking for"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger the Eligibility & Ranking Agent.

    Fetches all UAT_COMPLETED applications, calculates final scores,
    ranks them, runs greedy seat allocation, persists results,
    and transitions applications to PENDING_REVIEW.

    Only REGISTRAR_OFFICER or ADMIN can trigger this.
    """
    # ── Role gate ──
    if current_user.role not in {UserRole.REGISTRAR_OFFICER, UserRole.ADMIN}:
        raise HTTPException(403, "Only registrar officers or admins can trigger ranking")

    # ── 1. Fetch UAT_COMPLETED applications ──
    app_result = await db.execute(
        select(UndergraduateApplication).where(
            UndergraduateApplication.current_status == ApplicationStatus.UAT_COMPLETED,
            UndergraduateApplication.admission_term_id == admission_term_id,
            UndergraduateApplication.is_deleted == False,  # noqa: E712
        )
    )
    applications = app_result.scalars().all()

    if not applications:
        raise HTTPException(404, "No applications with UAT_COMPLETED status found")

    # ── 2. Fetch MoE records (keyed by admission_number) ──
    admission_numbers = [a.admission_number for a in applications]
    moe_result = await db.execute(
        select(MoeStudentRecord).where(
            MoeStudentRecord.admission_number.in_(admission_numbers)
        )
    )
    moe_map = {r.admission_number: r for r in moe_result.scalars().all()}

    # ── 3. Fetch UAT records (keyed by application_id) ──
    app_ids = [a.id for a in applications]
    uat_result = await db.execute(
        select(UATRecord).where(
            UATRecord.application_id.in_(app_ids),
            UATRecord.is_completed == True,  # noqa: E712
        )
    )
    uat_map = {r.application_id: r for r in uat_result.scalars().all()}

    # ── 4. Fetch program capacities ──
    prog_result = await db.execute(
        select(AcademicProgram).where(
            AcademicProgram.is_active == True,  # noqa: E712
            AcademicProgram.is_deleted == False,  # noqa: E712
        )
    )
    programs = prog_result.scalars().all()
    program_capacities = {}
    program_info = {}
    for p in programs:
        program_capacities[p.id] = p.max_capacity or 0
        program_info[str(p.id)] = {"code": p.code, "name": p.name, "stream": p.stream.value}

    # ── 5. Fetch stream quotas ──
    quota_result = await db.execute(
        select(StreamQuota).where(
            StreamQuota.is_deleted == False,  # noqa: E712
            StreamQuota.admission_term_id == admission_term_id,
        )
    )
    quotas = quota_result.scalars().all()
    stream_quotas = {q.stream.value: q.max_capacity for q in quotas}

    # Default quotas if not configured
    for st in StreamType:
        if st.value not in stream_quotas:
            stream_quotas[st.value] = 2500  # Default

    # ── 6. Resolve run number ──
    ranking_service = RankingService(db)
    run_number = await ranking_service.get_next_run_number(admission_term_id)

    # ── 7. Build applicant data ──
    from app.modules.undergraduate.agents.ranking_agent import ApplicantData, AGENT_VERSION, RankingState, build_ranking_graph

    applicant_list = []
    skipped = []
    for app in applications:
        moe = moe_map.get(app.admission_number)
        uat = uat_map.get(app.id)

        if moe is None or uat is None:
            skipped.append(str(app.id))
            continue

        applicant_list.append(ApplicantData(
            application_id=app.id,
            applicant_id=app.applicant_id,
            sponsorship_type=app.sponsorship_type.value,
            stream=app.stream.value,
            admission_number=app.admission_number,
            program_choice_1_id=app.program_choice_1_id,
            program_choice_2_id=app.program_choice_2_id,
            program_choice_3_id=app.program_choice_3_id,
            grade12_score=moe.total_score,
            uat_score=uat.score,
        ))

    if not applicant_list:
        raise HTTPException(400, f"No applicants with complete data. Skipped: {skipped}")

    # ── 8. Run the ranking agent ──
    batch_id = f"RANK-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    initial_state = RankingState(
        run_label=f"term={admission_term_id} run={run_number}",
        applicants=applicant_list,
        program_capacities=program_capacities,
        program_info=program_info,
        stream_quotas=stream_quotas,
    )

    compiled_graph = build_ranking_graph()
    final_state = compiled_graph.invoke(initial_state)

    # Re-runs use first run's cutoffs to assign applicants.
    if run_number > 1:
        try:
            await ranking_service.apply_first_run_cutoffs_for_rerun(
                term_id=admission_term_id,
                final_state=final_state,
                program_info=program_info,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    # ── 9. Persist results ──
    all_ranked = final_state["self_sponsored"] + final_state["government"]
    assigned_count = 0
    unassigned_count = 0

    svc = ApplicationService(db)

    for a in all_ranked:
        # Write RankingResult
        result_row = RankingResult(
            ranking_batch_id=batch_id,
            admission_term_id=admission_term_id,
            ranking_run_number=run_number,
            application_id=a.application_id,
            grade12_score=a.grade12_score,
            uat_score=a.uat_score,
            final_score=a.final_score,
            category=a.sponsorship_type,
            rank_position=a.rank_position,
            assigned_program_id=a.assigned_program_id,
            assigned_stream=StreamType(a.assigned_stream) if a.assigned_stream else None,
            is_assigned=a.is_assigned,
            assignment_detail=a.assignment_detail,
        )
        db.add(result_row)

        if a.is_assigned:
            assigned_count += 1
        else:
            unassigned_count += 1

        # Transition application to PENDING_REVIEW
        try:
            from app.modules.undergraduate.schemas import ApplicationStatusUpdate as StatusUpd
            await svc.change_status(
                a.application_id,
                StatusUpd(
                    new_status=ApplicationStatus.PENDING_REVIEW,
                    trigger_reason=(
                        f"Ranking run {run_number} for term {admission_term_id}: "
                        f"score={a.final_score}, rank={a.rank_position}, "
                        f"{'assigned' if a.is_assigned else 'unassigned'}"
                    ),
                ),
                actor_id=current_user.id,
                actor_role=UserRole.AGENT,
            )
        except Exception as e:
            # Log but don't fail the whole batch
            import logging
            logging.getLogger("ranking.router").warning(
                "Failed to transition app %s: %s", a.application_id, e
            )

    # ── 10. Update program cutoff scores ──
    for prog in programs:
        # Find the lowest final_score among assigned self-sponsored students for this program
        assigned_to_prog = [
            a for a in final_state["self_sponsored"]
            if a.assigned_program_id == prog.id and a.is_assigned
        ]
        if assigned_to_prog:
            cutoff = min(a.final_score for a in assigned_to_prog)
            prog.cut_off_score = cutoff

    # ── 11. Write AIEvaluation ──
    from app.ai.models import AIEvaluation, AIExecutionTrace
    from app.shared.enums import DecisionType

    # One evaluation per batch (summary)
    for a in all_ranked:
        evaluation = AIEvaluation(
            application_id=a.application_id,
            agent_version=AGENT_VERSION,
            recommended_decision=(
                DecisionType.RECOMMEND_ADMIT if a.is_assigned else DecisionType.RECOMMEND_REJECT
            ),
            confidence_score=min(a.final_score / 100, 1.0),
            is_overridden=False,
            summary_reasoning=(
                f"Ranking run {run_number} for term {admission_term_id} | Score: {a.final_score} | "
                f"Rank: {a.rank_position} | {a.assignment_detail}"
            ),
        )
        db.add(evaluation)
        await db.flush()

        # Write traces
        for trace in final_state["traces"]:
            db.add(AIExecutionTrace(
                evaluation_id=evaluation.id,
                step_name=trace["step_name"],
                reasoning_log=trace["reasoning_log"],
            ))

    await db.commit()

    return RankingRunResponse(
        term_id=admission_term_id,
        run_number=run_number,
        total_processed=len(all_ranked),
        self_sponsored_count=len(final_state["self_sponsored"]),
        government_count=len(final_state["government"]),
        assigned_count=assigned_count,
        unassigned_count=unassigned_count,
        message=(
            f"Ranking run {run_number} for term {admission_term_id} complete. "
            f"{assigned_count} assigned, {unassigned_count} unassigned."
        ),
    )


# ══════════════════════════════════════════════════════════════
#  GET /ranking/results/{term_id} — View ranked list
# ══════════════════════════════════════════════════════════════

@router.get("/results/{term_id}", response_model=list[RankingResultResponse])
async def get_ranking_results(
    term_id: uuid.UUID,
    category: str = Query(None, description="Filter by SELF_SPONSORED or GOVERNMENT"),
    db: AsyncSession = Depends(get_db),
):
    """Get all ranking rows for an admission term, optionally filtered by category."""
    query = select(RankingResult).where(
        RankingResult.admission_term_id == term_id,
    )

    if category:
        query = query.where(RankingResult.category == category.upper())

    query = query.order_by(
        RankingResult.ranking_run_number,
        RankingResult.category,
        RankingResult.rank_position,
    )

    result = await db.execute(query)
    items = result.scalars().all()

    if not items:
        raise HTTPException(404, f"No results found for term: {term_id}")

    application_ids = [item.application_id for item in items]
    app_result = await db.execute(
        select(UndergraduateApplication).where(UndergraduateApplication.id.in_(application_ids))
    )
    applications = {app.id: app for app in app_result.scalars().all()}

    applicant_ids = [app.applicant_id for app in applications.values()]
    user_result = await db.execute(select(User).where(User.id.in_(applicant_ids)))
    users = {user.id: user for user in user_result.scalars().all()}

    program_ids = [item.assigned_program_id for item in items if item.assigned_program_id is not None]
    program_result = await db.execute(select(AcademicProgram).where(AcademicProgram.id.in_(program_ids)))
    programs = {program.id: program for program in program_result.scalars().all()}

    return [
        {
            "id": item.id,
            "admission_term_id": item.admission_term_id,
            "ranking_run_number": item.ranking_run_number,
            "application_id": item.application_id,
            "applicant_full_name": (
                f"{users[applications[item.application_id].applicant_id].first_name} "
                f"{users[applications[item.application_id].applicant_id].last_name}"
                if applications.get(item.application_id)
                and users.get(applications[item.application_id].applicant_id)
                else None
            ),
            "grade12_score": item.grade12_score,
            "uat_score": item.uat_score,
            "final_score": item.final_score,
            "category": item.category,
            "rank_position": item.rank_position,
            "assigned_program_id": item.assigned_program_id,
            "assigned_program_department": (
                programs[item.assigned_program_id].department
                if item.assigned_program_id and programs.get(item.assigned_program_id)
                else None
            ),
            "assigned_stream": item.assigned_stream,
            "is_assigned": item.is_assigned,
            "assignment_detail": item.assignment_detail,
            "created_at": item.created_at,
        }
        for item in items
    ]


# ══════════════════════════════════════════════════════════════
#  GET /ranking/results/{term_id}/summary — Cutoffs + stats
# ══════════════════════════════════════════════════════════════

@router.get("/results/{term_id}/summary", response_model=RankingSummaryResponse)
async def get_ranking_summary(
    term_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get ranking summary aggregated across all runs for an admission term."""
    # Fetch all results for this term
    result = await db.execute(
        select(RankingResult).where(
            RankingResult.admission_term_id == term_id,
        )
    )
    results = result.scalars().all()
    if not results:
        raise HTTPException(404, f"No results found for term: {term_id}")

    # Program cutoffs (self-sponsored)
    prog_result = await db.execute(
        select(AcademicProgram).where(
            AcademicProgram.is_active == True,  # noqa: E712
            AcademicProgram.is_deleted == False,  # noqa: E712
        )
    )
    programs = {p.id: p for p in prog_result.scalars().all()}

    program_cutoffs = []
    prog_assignments = {}  # {prog_id: [final_scores]}
    for r in results:
        if r.assigned_program_id and r.is_assigned:
            prog_assignments.setdefault(r.assigned_program_id, []).append(r.final_score)

    for prog_id, scores in prog_assignments.items():
        prog = programs.get(prog_id)
        if prog:
            program_cutoffs.append(ProgramCutoffResponse(
                program_id=prog.id,
                program_code=prog.code,
                program_name=prog.name,
                stream=prog.stream,
                max_capacity=prog.max_capacity,
                assigned_count=len(scores),
                cutoff_score=min(scores),
            ))

    # Stream cutoffs (government)
    quota_result = await db.execute(
        select(StreamQuota).where(
            StreamQuota.is_deleted == False,  # noqa: E712
            StreamQuota.admission_term_id == term_id,
        )
    )
    quotas = {q.stream.value: q.max_capacity for q in quota_result.scalars().all()}

    stream_assignments = {}  # {stream: [final_scores]}
    for r in results:
        if r.assigned_stream and r.is_assigned:
            stream_assignments.setdefault(r.assigned_stream.value, []).append(r.final_score)

    stream_cutoffs = []
    for stream_val in [StreamType.NATURAL.value, StreamType.SOCIAL.value]:
        scores = stream_assignments.get(stream_val, [])
        stream_cutoffs.append(StreamCutoffResponse(
            stream=StreamType(stream_val),
            max_capacity=quotas.get(stream_val, 2500),
            assigned_count=len(scores),
            cutoff_score=min(scores) if scores else None,
        ))

    total_assigned = sum(1 for r in results if r.is_assigned)
    total_unassigned = sum(1 for r in results if not r.is_assigned)

    return RankingSummaryResponse(
        term_id=term_id,
        program_cutoffs=program_cutoffs,
        stream_cutoffs=stream_cutoffs,
        total_assigned=total_assigned,
        total_unassigned=total_unassigned,
    )


# ══════════════════════════════════════════════════════════════
#  Stream Quota Management
# ══════════════════════════════════════════════════════════════

@router.get("/stream-quotas", response_model=list[StreamQuotaResponse])
async def list_stream_quotas(
    admission_term_id: uuid.UUID = Query(..., description="Admission term ID"),
    db: AsyncSession = Depends(get_db),
):
    """List all stream quotas."""
    result = await db.execute(
        select(StreamQuota).where(StreamQuota.is_deleted == False)  # noqa: E712
        .where(StreamQuota.admission_term_id == admission_term_id)
    )
    return result.scalars().all()


@router.post("/stream-quotas", response_model=StreamQuotaResponse, status_code=201)
async def create_stream_quota(
    data: StreamQuotaCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a stream quota for a specific admission term (admin only)."""
    if current_user.role not in {UserRole.REGISTRAR_OFFICER, UserRole.ADMIN}:
        raise HTTPException(403, "Only officers or admins can create quotas")

    term = (await db.execute(
        select(UndergraduateAdmissionTerm).where(
            UndergraduateAdmissionTerm.id == data.admission_term_id,
            UndergraduateAdmissionTerm.is_deleted == False,  # noqa: E712
        )
    )).scalar_one_or_none()
    if term is None:
        raise HTTPException(404, "Admission term not found")

    existing = (await db.execute(
        select(StreamQuota).where(
            StreamQuota.stream == data.stream,
            StreamQuota.admission_term_id == data.admission_term_id,
            StreamQuota.is_deleted == False,  # noqa: E712
        )
    )).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(409, f"Quota already exists for {data.stream.value} in this admission term")

    quota = StreamQuota(
        stream=data.stream,
        max_capacity=data.max_capacity,
        admission_term_id=data.admission_term_id,
    )
    db.add(quota)
    await db.commit()
    await db.refresh(quota)
    return quota


@router.put("/stream-quotas/{stream}", response_model=StreamQuotaResponse)
async def update_stream_quota(
    stream: StreamType,
    data: StreamQuotaUpdate,
    admission_term_id: uuid.UUID = Query(..., description="Admission term ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a stream quota (admin only)."""
    if current_user.role not in {UserRole.REGISTRAR_OFFICER, UserRole.ADMIN}:
        raise HTTPException(403, "Only officers or admins can update quotas")

    result = await db.execute(
        select(StreamQuota).where(
            StreamQuota.stream == stream,
            StreamQuota.admission_term_id == admission_term_id,
            StreamQuota.is_deleted == False,  # noqa: E712
        )
    )
    quota = result.scalar_one_or_none()
    if quota is None:
        raise HTTPException(404, f"No quota configured for stream: {stream.value} in this admission term")

    quota.max_capacity = data.max_capacity
    await db.commit()
    await db.refresh(quota)
    return quota
