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

from app.core.dependencies import get_current_user, get_email_service
from app.database.session import get_db
from app.modules.auth.models import User
from app.modules.course.exceptions import (
    EntityNotFoundError,
    StudentAlreadyOnboardedError,
    UnauthorizedActorError,
)
from app.modules.course.service import OnboardingService
from app.modules.undergraduate.enrollment.models import Enrollment
from app.modules.undergraduate.enrollment.schemas import (
    EnrollmentListResponse,
    EnrollmentResponse,
    EnrollmentRunResponse,
    OnboardingOutcome,
)
from app.modules.programs.models import AcademicProgram
from app.modules.undergraduate.ranking.models import RankingResult
from app.modules.undergraduate.models import (
    RegistrarDecision,
    UndergraduateApplication,
)
from app.modules.undergraduate.service import ApplicationService
from app.modules.undergraduate.schemas import ApplicationStatusUpdate
from app.shared.email import EmailService
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
    term_id: uuid.UUID = Query(..., description="Admission term ID to enroll"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    email_service: EmailService = Depends(get_email_service),
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
            UndergraduateApplication.admission_term_id == term_id,
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
    # Each applicant has one RankingResult row per run; the latest run holds
    # the authoritative locked assignment. Order desc and keep first per app.
    enroll_ids = [a.id for a in to_enroll]
    rr_result = await db.execute(
        select(RankingResult)
        .where(RankingResult.application_id.in_(enroll_ids))
        .order_by(RankingResult.ranking_run_number.desc())
    )
    rr_map: dict[uuid.UUID, RankingResult] = {}
    for r in rr_result.scalars().all():
        rr_map.setdefault(r.application_id, r)

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
            admission_term_id=app.admission_term_id,
            admission_number=app.admission_number,
            admission_term=app.admission_term.term_name if app.admission_term else "Unknown",
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
        "term_id": term_id,
        "traces": [],
    }

    compiled_graph = build_enrollment_graph()
    final_state = compiled_graph.invoke(initial_state)

    # ── 7. Persist enrollment records + auto-onboard each student ──
    #
    # Each iteration runs the full per-student pipeline so a failure
    # on one student doesn't poison the rest:
    #   1. INSERT Enrollment
    #   2. change_status → ENROLLED (commits the session)
    #   3. OnboardingService: mint 4-digit PIN, set must_change_password,
    #      create Student row, email credentials (best-effort SMTP)
    #
    # Re-running /enrollment/run is safe: step (2) is short-circuited
    # by the existing-enrollment filter above, and step (3) raises
    # StudentAlreadyOnboardedError which we treat as "skip, already done".
    import logging
    log = logging.getLogger("enrollment.router")

    svc = ApplicationService(db)
    onboarding_svc = OnboardingService(db, email_service=email_service)
    onboarding_outcomes: list[OnboardingOutcome] = []
    onboarded_count = 0

    for s in final_state["students"]:
        enrollment = Enrollment(
            application_id=s.application_id,
            applicant_id=s.applicant_id,
            admission_term_id=s.admission_term_id,
            university_id=s.university_id,
            program_id=s.assigned_program_id,
            department=s.assigned_department or s.stream,
            enrollment_term=s.admission_term,
        )
        db.add(enrollment)

        # Transition to ENROLLED. change_status commits, so the
        # Enrollment row is durable and addressable by id before
        # onboarding tries to load it back.
        try:
            await svc.change_status(
                s.application_id,
                ApplicationStatusUpdate(
                    new_status=ApplicationStatus.ENROLLED,
                    trigger_reason=(
                        f"Enrolled as {s.university_id} "
                        f"— {s.assigned_department or s.stream}"
                    ),
                ),
                actor_id=current_user.id,
                actor_role=UserRole.AGENT,
            )
        except Exception as e:
            log.warning(
                "Failed to transition app %s: %s", s.application_id, e
            )
            onboarding_outcomes.append(OnboardingOutcome(
                application_id=s.application_id,
                university_id=s.university_id,
                onboarded=False,
                detail=f"Status transition failed: {e}",
            ))
            continue

        try:
            await onboarding_svc.onboard_student_from_enrollment(
                enrollment_id=enrollment.id,
                officer_role=current_user.role,
                officer_id=current_user.id,
            )
            onboarded_count += 1
            onboarding_outcomes.append(OnboardingOutcome(
                application_id=s.application_id,
                university_id=s.university_id,
                onboarded=True,
                detail="Portal credentials issued and emailed.",
            ))
        except StudentAlreadyOnboardedError as exc:
            onboarding_outcomes.append(OnboardingOutcome(
                application_id=s.application_id,
                university_id=s.university_id,
                onboarded=False,
                detail=f"Already onboarded as {exc.student_id}.",
            ))
        except (UnauthorizedActorError, EntityNotFoundError) as exc:
            log.warning(
                "Onboarding failed for %s: %s", s.application_id, exc
            )
            onboarding_outcomes.append(OnboardingOutcome(
                application_id=s.application_id,
                university_id=s.university_id,
                onboarded=False,
                detail=str(exc),
            ))
        except Exception as exc:
            log.exception(
                "Onboarding failed for %s", s.application_id
            )
            onboarding_outcomes.append(OnboardingOutcome(
                application_id=s.application_id,
                university_id=s.university_id,
                onboarded=False,
                detail=f"Unexpected error: {exc}",
            ))

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
        onboarded_count=onboarded_count,
        message=(
            f"Enrolled {len(final_state['students'])} students "
            f"({onboarded_count} onboarded with portal credentials). "
            f"{skipped} skipped (already enrolled)."
        ),
        enrollments=[EnrollmentResponse.model_validate(e) for e in fresh_enrollments],
        onboarding_outcomes=onboarding_outcomes,
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
    term_id: uuid.UUID = Query(..., description="Admission term ID to filter by"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Paginated list of all enrollment records."""
    total = (await db.execute(
        select(func.count(Enrollment.id)).where(Enrollment.admission_term_id == term_id)
    )).scalar()

    offset = (page - 1) * page_size
    result = await db.execute(
        select(Enrollment)
        .where(Enrollment.admission_term_id == term_id)
        .order_by(Enrollment.university_id)
        .offset(offset)
        .limit(page_size)
    )
    items = result.scalars().all()

    application_ids = [item.application_id for item in items]
    app_result = await db.execute(
        select(UndergraduateApplication).where(UndergraduateApplication.id.in_(application_ids))
    )
    applications = {app.id: app for app in app_result.scalars().all()}

    applicant_ids = [app.applicant_id for app in applications.values()]
    user_result = await db.execute(select(User).where(User.id.in_(applicant_ids)))
    users = {user.id: user for user in user_result.scalars().all()}

    return EnrollmentListResponse(
        items=[
            {
                "id": item.id,
                "application_id": item.application_id,
                "applicant_id": item.applicant_id,
                "student_full_name": (
                    f"{users[applications[item.application_id].applicant_id].first_name} "
                    f"{users[applications[item.application_id].applicant_id].last_name}"
                    if applications.get(item.application_id)
                    and users.get(applications[item.application_id].applicant_id)
                    else None
                ),
                "admission_term_id": item.admission_term_id,
                "university_id": item.university_id,
                "program_id": item.program_id,
                "department": item.department,
                "enrollment_term": item.enrollment_term,
                "created_at": item.created_at,
            }
            for item in items
        ],
        total=total,
        page=page,
        page_size=page_size,
    )
