"""
Enrollment module — FastAPI router.

Provides endpoints for triggering enrollment, viewing enrollment
details, and listing enrolled students.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.database.session import get_db
from app.modules.auth.models import User
from app.modules.undergraduate.enrollment.models import Enrollment
from app.modules.undergraduate.enrollment.schemas import (
    EnrollmentListResponse,
    EnrollmentResponse,
    EnrollmentRunResponse,
)
from app.modules.programs.models import AcademicProgram
from app.modules.undergraduate.ranking.models import RankingResult
from app.modules.undergraduate.models import (
    RegistrarDecision,
    UndergraduateApplication,
)
from app.modules.undergraduate.service import ApplicationService
from app.modules.undergraduate.schemas import ApplicationStatusUpdate
from app.shared.enums import (
    ApplicationStatus,
    DecisionType,
    UserRole,
)

router = APIRouter(prefix="/undergraduate/enrollment", tags=["Undergraduate Enrollment & Onboarding"])


# ══════════════════════════════════════════════════════════════
#  POST /enrollment/run — Trigger enrollment batch
# ══════════════════════════════════════════════════════════════

@router.post("/run", response_model=EnrollmentRunResponse)
async def run_enrollment(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger the Enrollment & Onboarding Agent.

    Finds all DECIDED + ADMIT applications (without an existing
    enrollment record), generates university IDs, temp passwords,
    assigns sections, and transitions to ENROLLED.

    Only REGISTRAR_OFFICER or ADMIN can trigger this.
    """
    if current_user.role not in {UserRole.REGISTRAR_OFFICER, UserRole.ADMIN}:
        raise HTTPException(403, "Only registrar officers or admins can trigger enrollment")

    # ── 1. Fetch DECIDED + ADMIT applications ──
    app_result = await db.execute(
        select(UndergraduateApplication).where(
            UndergraduateApplication.current_status == ApplicationStatus.DECIDED,
            UndergraduateApplication.final_decision == DecisionType.ADMIT.value,
            UndergraduateApplication.is_deleted == False,  # noqa: E712
        )
    )
    applications = app_result.scalars().all()

    if not applications:
        raise HTTPException(404, "No admitted applications awaiting enrollment")

    # ── 2. Filter out already-enrolled ──
    app_ids = [a.id for a in applications]
    existing_enrollments = (await db.execute(
        select(Enrollment.application_id).where(
            Enrollment.application_id.in_(app_ids)
        )
    )).scalars().all()
    existing_set = set(existing_enrollments)

    to_enroll = [a for a in applications if a.id not in existing_set]
    skipped = len(applications) - len(to_enroll)

    if not to_enroll:
        raise HTTPException(400, "All admitted applications are already enrolled")

    # ── 3. Fetch ranking results for program assignment ──
    enroll_ids = [a.id for a in to_enroll]
    rr_result = await db.execute(
        select(RankingResult).where(
            RankingResult.application_id.in_(enroll_ids)
        )
    )
    rr_map = {r.application_id: r for r in rr_result.scalars().all()}

    # ── 4. Fetch program info ──
    prog_result = await db.execute(select(AcademicProgram))
    prog_map = {p.id: p for p in prog_result.scalars().all()}

    # ── 5. Find the current highest university ID counter ──
    max_uid_result = await db.execute(
        select(Enrollment.university_id).order_by(Enrollment.created_at.desc()).limit(1)
    )
    last_uid = max_uid_result.scalar_one_or_none()

    if last_uid:
        # Parse "UGR/XXXX/YY" → XXXX
        parts = last_uid.split("/")
        counter_start = int(parts[1]) + 1
    else:
        counter_start = 6400  # Starting ID

    year_suffix = datetime.now().strftime("%y")  # "26"

    # ── 6. Build admitted student data and run agent ──
    from app.modules.undergraduate.agents.enrollment_agent import (
        AdmittedStudent,
        build_enrollment_graph,
    )

    student_list = []
    for app in to_enroll:
        rr = rr_map.get(app.id)
        prog = prog_map.get(rr.assigned_program_id) if rr and rr.assigned_program_id else None

        student_list.append(AdmittedStudent(
            application_id=app.id,
            applicant_id=app.applicant_id,
            admission_number=app.admission_number,
            admission_term=app.admission_term,
            sponsorship_type=app.sponsorship_type.value,
            stream=app.stream.value,
            assigned_program_id=rr.assigned_program_id if rr else None,
            assigned_program_name=prog.name if prog else None,
            assigned_program_code=prog.code if prog else None,
            assigned_department=prog.department if prog else app.stream.value,
        ))

    initial_state = {
        "students": student_list,
        "id_counter_start": counter_start,
        "year_suffix": year_suffix,
        "section_capacity": 50,
        "traces": [],
    }

    compiled_graph = build_enrollment_graph()
    final_state = compiled_graph.invoke(initial_state)

    # ── 7. Persist enrollment records ──
    svc = ApplicationService(db)
    enrollment_responses = []

    for s in final_state["students"]:
        enrollment = Enrollment(
            application_id=s.application_id,
            applicant_id=s.applicant_id,
            university_id=s.university_id,
            portal_password=s.portal_password,
            program_id=s.assigned_program_id,
            department=s.assigned_department or s.stream,
            section=s.section,
            enrollment_term=s.admission_term,
        )
        db.add(enrollment)

        # Transition to ENROLLED
        try:
            await svc.change_status(
                s.application_id,
                ApplicationStatusUpdate(
                    new_status=ApplicationStatus.ENROLLED,
                    trigger_reason=f"Enrolled as {s.university_id} — {s.assigned_department or s.stream} Section {s.section}",
                ),
                actor_id=current_user.id,
                actor_role=UserRole.AGENT,
            )
        except Exception as e:
            import logging
            logging.getLogger("enrollment.router").warning(
                "Failed to transition app %s: %s", s.application_id, e
            )

    await db.commit()

    # Re-fetch to get created_at timestamps
    fresh_result = await db.execute(
        select(Enrollment).where(
            Enrollment.application_id.in_(enroll_ids)
        )
    )
    fresh_enrollments = fresh_result.scalars().all()

    return EnrollmentRunResponse(
        enrolled_count=len(final_state["students"]),
        skipped_count=skipped,
        message=f"Enrolled {len(final_state['students'])} students. {skipped} skipped (already enrolled).",
        enrollments=[EnrollmentResponse.model_validate(e) for e in fresh_enrollments],
    )


# ══════════════════════════════════════════════════════════════
#  GET /enrollment/{application_id} — Get enrollment details
# ══════════════════════════════════════════════════════════════

@router.get("/{application_id}", response_model=EnrollmentResponse)
async def get_enrollment(
    application_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get enrollment details for a specific application."""
    enrollment = (await db.execute(
        select(Enrollment).where(Enrollment.application_id == application_id)
    )).scalar_one_or_none()

    if not enrollment:
        raise HTTPException(404, "Enrollment record not found for this application")

    return enrollment


# ══════════════════════════════════════════════════════════════
#  GET /enrollment/list — Paginated list
# ══════════════════════════════════════════════════════════════

@router.get("/list/all", response_model=EnrollmentListResponse)
async def list_enrollments(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Paginated list of all enrollment records."""
    total = (await db.execute(
        select(func.count(Enrollment.id))
    )).scalar()

    offset = (page - 1) * page_size
    result = await db.execute(
        select(Enrollment)
        .order_by(Enrollment.university_id)
        .offset(offset)
        .limit(page_size)
    )
    items = result.scalars().all()

    return EnrollmentListResponse(
        items=[EnrollmentResponse.model_validate(e) for e in items],
        total=total,
        page=page,
        page_size=page_size,
    )
