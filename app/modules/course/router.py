"""
Course Management — FastAPI router (Track A foundation).

Mounted under ``/api/v1/courses`` by app/main.py.

Endpoints in this commit:

  Officer (registrar/admin):
    POST   /officer/terms/{term_id}/open      open the registration window
    POST   /officer/terms/{term_id}/close     close the registration window

  Student:
    GET    /me/curriculum                     list courses the student can register for
    POST   /registrations                     create a draft registration
    GET    /registrations/{id}                read a draft / finalised registration
    POST   /registrations/{id}/courses        add a course to a draft
    DELETE /registrations/{id}/courses/{cid}  remove a course from a draft
    POST   /registrations/{id}/submit         submit + run the compliance agent

Endpoint role-guarding follows the existing app.core.dependencies
pattern: get_current_user resolves the User, the route checks role,
and the service then resolves the matching Student row.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_email_service
from app.database.session import get_db
from app.modules.auth.models import User
from app.modules.course.exceptions import (
    AdjustmentDeniedError,
    ComplianceCheckFailedError,
    DuplicateRegistrationError,
    EntityNotFoundError,
    InvalidAdjustmentRequestError,
    InvalidStateTransitionError,
    RegistrationWindowClosedError,
    UnauthorizedActorError,
)
from app.modules.course.repository import StudentRepository
from app.modules.course.schemas import (
    AcademicTermResponse,
    AddDropOverrideRequest,
    AddDropRequestCreate,
    AddDropRequestResponse,
    AdvisoryEvaluateRequest,
    AdvisoryRecommendationRead,
    AdvisoryReviewCloseRequest,
    ComplianceResultResponse,
    CourseResponse,
    RegistrationCourseAdd,
    RegistrationDraftCreate,
    RegistrationResponse,
    RegistrationSubmitResponse,
    ScheduleConflictRead,
    ScheduleGenerateRequest,
    ScheduleGenerateResponse,
    SectionTimetableEntry,
    TimetableResponse,
)
from app.modules.course.service import (
    AddDropService, AdvisoryService, RegistrationService,
    SchedulingService, TermService,
)
from app.shared.email.service import EmailService
from app.shared.enums import UserRole


router = APIRouter(prefix="/courses", tags=["Course Management"])


# ── Officer endpoints ────────────────────────────────────────────


@router.post(
    "/officer/terms/{term_id}/open",
    response_model=AcademicTermResponse,
)
async def open_registration_window(
    term_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = TermService(db)
    try:
        term = await svc.open_window(
            term_id, current_user.role, current_user.id,
        )
    except UnauthorizedActorError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.detail)
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    return term


@router.post(
    "/officer/terms/{term_id}/close",
    response_model=AcademicTermResponse,
)
async def close_registration_window(
    term_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = TermService(db)
    try:
        term = await svc.close_window(
            term_id, current_user.role, current_user.id,
        )
    except UnauthorizedActorError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.detail)
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    return term


# ── Student endpoints ────────────────────────────────────────────


async def _resolve_student(db: AsyncSession, user: User):
    """
    Look up the Student row corresponding to the calling User. Raises
    HTTP 403 if the caller has no student profile (e.g. an officer).
    """
    if user.role != UserRole.STUDENT:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only students may use the registration portal.",
        )
    student = await StudentRepository(db).get_by_user_id(user.id)
    if student is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Calling user has no student profile.",
        )
    return student


@router.get("/me/curriculum", response_model=list[CourseResponse])
async def list_my_curriculum(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    student = await _resolve_student(db, current_user)
    svc = RegistrationService(db)
    return await svc.list_curriculum_courses(student.id)


@router.post(
    "/registrations",
    response_model=RegistrationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_registration_draft(
    payload: RegistrationDraftCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    student = await _resolve_student(db, current_user)
    svc = RegistrationService(db)
    try:
        return await svc.create_draft(
            student.id, payload.term_id, payload.sponsorship_type,
        )
    except RegistrationWindowClosedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except DuplicateRegistrationError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


@router.get(
    "/registrations/{registration_id}",
    response_model=RegistrationResponse,
)
async def get_registration(
    registration_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    student = await _resolve_student(db, current_user)
    svc = RegistrationService(db)
    registration = await svc.registrations.get(registration_id)
    if registration is None or registration.student_id != student.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Registration not found.")
    return registration


@router.post(
    "/registrations/{registration_id}/courses",
    response_model=RegistrationResponse,
)
async def add_course_to_draft(
    registration_id: uuid.UUID,
    payload: RegistrationCourseAdd,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    student = await _resolve_student(db, current_user)
    svc = RegistrationService(db)
    try:
        registration = await svc.add_course_to_draft(
            registration_id, payload.course_id,
        )
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    except InvalidStateTransitionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if registration.student_id != student.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your registration.")
    return registration


@router.delete(
    "/registrations/{registration_id}/courses/{course_id}",
    response_model=RegistrationResponse,
)
async def remove_course_from_draft(
    registration_id: uuid.UUID,
    course_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    student = await _resolve_student(db, current_user)
    svc = RegistrationService(db)
    registration = await svc.registrations.get(registration_id)
    if registration is None or registration.student_id != student.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Registration not found.")
    try:
        return await svc.remove_course_from_draft(registration_id, course_id)
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    except InvalidStateTransitionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))


@router.post(
    "/registrations/{registration_id}/submit",
    response_model=RegistrationSubmitResponse,
)
async def submit_registration(
    registration_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    student = await _resolve_student(db, current_user)
    svc = RegistrationService(db)
    registration = await svc.registrations.get(registration_id)
    if registration is None or registration.student_id != student.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Registration not found.")
    try:
        registration, compliance = await svc.submit(
            registration_id, current_user.id,
        )
    except ComplianceCheckFailedError as exc:
        # 422 carries the structured agent payload so the portal can
        # show plain-language reasons against each course.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"compliance": exc.payload},
        )
    except InvalidStateTransitionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return RegistrationSubmitResponse(
        registration=RegistrationResponse.model_validate(registration),
        compliance=ComplianceResultResponse(**compliance),
    )


# ── Scheduling endpoints ─────────────────────────────────────────


@router.post(
    "/officer/schedule/generate",
    response_model=ScheduleGenerateResponse,
)
async def officer_generate_schedule(
    payload: ScheduleGenerateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = SchedulingService(db)
    try:
        result = await svc.generate_schedule(
            term_id=payload.term_id,
            department=payload.department,
            officer_role=current_user.role,
            officer_id=current_user.id,
        )
    except UnauthorizedActorError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.detail)
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    return ScheduleGenerateResponse(**result)


@router.get(
    "/officer/schedule/conflicts",
    response_model=list[ScheduleConflictRead],
)
async def officer_list_conflicts(
    term_id: uuid.UUID,
    department: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = SchedulingService(db)
    try:
        rows = await svc.list_open_conflicts(
            term_id=term_id,
            officer_role=current_user.role,
            department=department,
        )
    except UnauthorizedActorError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.detail)
    return rows


@router.get(
    "/me/timetable",
    response_model=TimetableResponse,
)
async def get_my_timetable(
    term_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    student = await _resolve_student(db, current_user)
    svc = SchedulingService(db)
    rows = await svc.get_student_timetable(student.id, term_id)
    return TimetableResponse(
        term_id=term_id,
        entries=[SectionTimetableEntry(**r) for r in rows],
    )


@router.get(
    "/instructors/{instructor_id}/timetable",
    response_model=TimetableResponse,
)
async def get_instructor_timetable(
    instructor_id: uuid.UUID,
    term_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Read-only instructor timetable. Authorisation is permissive in
    Phase 1: any authenticated user can view any instructor's
    timetable. Tighten when role-aware instructor identity wiring
    lands later.
    """
    del current_user  # auth confirmed by Depends; no role gate yet
    svc = SchedulingService(db)
    rows = await svc.get_instructor_timetable(instructor_id, term_id)
    return TimetableResponse(
        term_id=term_id,
        entries=[SectionTimetableEntry(**r) for r in rows],
    )


# ── Add/Drop endpoints ───────────────────────────────────────────


@router.post(
    "/add-drop/requests",
    response_model=AddDropRequestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_add_drop_request(
    payload: AddDropRequestCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    email_service: EmailService = Depends(get_email_service),
):
    student = await _resolve_student(db, current_user)

    # Confirm the registration belongs to the calling student.
    svc = AddDropService(db, email_service=email_service)
    registration = await svc.registrations.get(payload.registration_id)
    if registration is None or registration.student_id != student.id:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Registration not found."
        )

    try:
        request = await svc.submit_request(
            registration_id=payload.registration_id,
            course_id=payload.course_id,
            action=payload.action,
            deadline=payload.deadline,
            student_user_id=current_user.id,
            target_section_id=payload.target_section_id,
        )
    except AdjustmentDeniedError as exc:
        # 422 carries the agent verdict so the portal can show
        # plain-language reasons against the failed request.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"compliance": exc.payload},
        )
    except InvalidAdjustmentRequestError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, exc.detail)
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    return request


@router.get(
    "/add-drop/requests/{request_id}",
    response_model=AddDropRequestResponse,
)
async def get_add_drop_request(
    request_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = AddDropService(db)
    request = await svc.get(request_id)
    if request is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Add/drop request not found."
        )
    # Owner OR officer may read.
    if current_user.role not in {
        UserRole.REGISTRAR_OFFICER, UserRole.ADMIN,
    }:
        student = await _resolve_student(db, current_user)
        registration = await svc.registrations.get(request.registration_id)
        if registration is None or registration.student_id != student.id:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "Add/drop request not found."
            )
    return request


@router.get(
    "/registrations/{registration_id}/add-drop-requests",
    response_model=list[AddDropRequestResponse],
)
async def list_add_drop_requests(
    registration_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    student = await _resolve_student(db, current_user)
    svc = AddDropService(db)
    registration = await svc.registrations.get(registration_id)
    if registration is None or registration.student_id != student.id:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Registration not found."
        )
    return await svc.list_for_registration(registration_id)


@router.post(
    "/officer/add-drop/{request_id}/override",
    response_model=AddDropRequestResponse,
)
async def officer_override_add_drop(
    request_id: uuid.UUID,
    payload: AddDropOverrideRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    email_service: EmailService = Depends(get_email_service),
):
    svc = AddDropService(db, email_service=email_service)
    try:
        return await svc.officer_override(
            request_id=request_id,
            officer_role=current_user.role,
            officer_id=current_user.id,
            justification=payload.justification,
        )
    except UnauthorizedActorError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.detail)
    except InvalidAdjustmentRequestError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, exc.detail)
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


# ── Advisory endpoints ───────────────────────────────────────────


@router.post(
    "/advisory/evaluate",
    response_model=AdvisoryRecommendationRead,
    status_code=status.HTTP_201_CREATED,
)
async def evaluate_advisory_plan(
    payload: AdvisoryEvaluateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Student-triggered advisory evaluation. Persists an
    AdvisoryRecommendation row and returns the agent's verdict.
    HIGH-risk verdicts auto-flag for officer review (the row appears
    in the officer queue immediately).
    """
    student = await _resolve_student(db, current_user)
    svc = AdvisoryService(db)
    try:
        return await svc.evaluate_plan(
            student_id=student.id,
            term_id=payload.term_id,
            proposed_course_ids=payload.proposed_course_ids,
            cgpa=payload.cgpa,
            completed_course_ids=set(payload.completed_course_ids),
        )
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


@router.get(
    "/advisory/me/recommendations",
    response_model=list[AdvisoryRecommendationRead],
)
async def list_my_advisory_recommendations(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    student = await _resolve_student(db, current_user)
    svc = AdvisoryService(db)
    return await svc.list_for_student(student.id)


@router.get(
    "/advisory/recommendations/{recommendation_id}",
    response_model=AdvisoryRecommendationRead,
)
async def get_advisory_recommendation(
    recommendation_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = AdvisoryService(db)
    rec = await svc.get(recommendation_id)
    if rec is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Advisory recommendation not found."
        )
    # Owner OR officer may read.
    if current_user.role not in {
        UserRole.REGISTRAR_OFFICER, UserRole.ADMIN,
    }:
        student = await _resolve_student(db, current_user)
        if rec.student_id != student.id:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                "Advisory recommendation not found.",
            )
    return rec


@router.get(
    "/officer/advisory/high-risk",
    response_model=list[AdvisoryRecommendationRead],
)
async def list_high_risk_advisory_queue(
    term_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Officer-only queue of pending HIGH-risk advisory verdicts.
    """
    svc = AdvisoryService(db)
    try:
        return await svc.list_high_risk_open(
            term_id=term_id, officer_role=current_user.role,
        )
    except UnauthorizedActorError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.detail)


@router.post(
    "/officer/advisory/{recommendation_id}/close",
    response_model=AdvisoryRecommendationRead,
)
async def officer_close_advisory_review(
    recommendation_id: uuid.UUID,
    payload: AdvisoryReviewCloseRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = AdvisoryService(db)
    try:
        return await svc.close_officer_review(
            recommendation_id=recommendation_id,
            officer_role=current_user.role,
            officer_id=current_user.id,
            review_notes=payload.review_notes,
        )
    except UnauthorizedActorError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.detail)
    except InvalidAdjustmentRequestError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, exc.detail)
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
