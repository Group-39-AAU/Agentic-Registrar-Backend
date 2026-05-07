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
from typing import Optional

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
    StudentAlreadyOnboardedError,
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
    CostSharingFormResponse,
    CourseResponse,
    InstructorAssignmentCreate,
    InstructorAssignmentResponse,
    InstructorCreateRequest,
    InstructorResponse,
    InstructorScheduleEntry,
    RegistrationCourseAdd,
    RegistrationDraftCreate,
    RegistrationResponse,
    RegistrationSubmitResponse,
    ScheduleConflictRead,
    ScheduleGenerateRequest,
    ScheduleGenerateResponse,
    SectionScheduleResponse,
    PrerequisiteOverrideRequest,
    PrerequisiteOverrideResponse,
    RegistrationPaymentCallbackRequest,
    RegistrationPaymentInitiateResponse,
    StudentDashboardResponse,
    StudentOnboardRequest,
    StudentResponse,
)
from app.modules.course.service import (
    AddDropService, AdvisoryService, InstructorService, OnboardingService,
    RegistrationService, SchedulingService, TermService,
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


@router.get(
    "/me",
    response_model=StudentDashboardResponse,
    summary="Student dashboard — name, UGR id, department, term, section",
)
async def get_my_dashboard(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Consolidated identity + current-term context for the calling
    student: full_name, email, UGR id, department, semester,
    sponsorship_type, enrollment_status, and (when a term is open)
    the term_name + cohort section.

    ``current_term.section`` is null until the officer has run
    ``POST /officer/schedule/generate`` for the term.
    """
    student = await _resolve_student(db, current_user)
    svc = RegistrationService(db)
    return await svc.get_student_dashboard(student.id)


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
        return await svc.create_draft(student.id, payload.term_id)
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
    """
    Officer-only: allocate every REGISTERED student in the term to a
    cohort Section, then build the per-section weekly schedule. Runs
    across all departments at once (cohort allocation is term-wide).
    """
    svc = SchedulingService(db)
    try:
        result = await svc.generate_schedule(
            term_id=payload.term_id,
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
    "/me/schedule",
    response_model=SectionScheduleResponse,
    summary="Calling student's section schedule for a term",
)
async def get_my_schedule(
    term_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Return the calling student's class schedule for ``term_id``.
    Resolves the student → registration → section → slots. ``section``
    is null and ``slots`` is empty when the student has not been
    placed into a section yet (officer hasn't run scheduling).
    """
    student = await _resolve_student(db, current_user)
    svc = SchedulingService(db)
    return await svc.get_student_schedule(student.id, term_id)


@router.get(
    "/students/{student_id}/schedule",
    response_model=SectionScheduleResponse,
    summary="Look up any student's section schedule (officer/admin)",
)
async def get_student_schedule_by_id(
    student_id: uuid.UUID,
    term_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Officer/admin endpoint to query any student's class schedule by
    student_id. Distinct from /me/schedule which resolves the caller
    automatically.
    """
    if current_user.role not in {UserRole.REGISTRAR_OFFICER, UserRole.ADMIN}:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only registrar officers or admins may look up other students' schedules.",
        )
    svc = SchedulingService(db)
    return await svc.get_student_schedule(student_id, term_id)


@router.get(
    "/instructors/{instructor_id}/schedule",
    response_model=list[InstructorScheduleEntry],
)
async def get_instructor_schedule(
    instructor_id: uuid.UUID,
    term_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Read-only instructor schedule, flattened across sections. Any
    authenticated user can view any instructor's schedule
    (consistent with the previous timetable endpoint's policy).
    """
    del current_user  # auth confirmed by Depends; no role gate
    svc = SchedulingService(db)
    return await svc.get_instructor_schedule(instructor_id, term_id)


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
    "/officer/registrations/{registration_id}/prerequisite-override",
    response_model=PrerequisiteOverrideResponse,
    status_code=status.HTTP_201_CREATED,
)
async def department_head_grant_prerequisite_override(
    registration_id: uuid.UUID,
    payload: PrerequisiteOverrideRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Department-Head-only endpoint to bypass the prerequisite check
    for a single (registration, course) pair per SRS §3.5 inverse
    requirement.

    Authorisation: ``current_user.role`` must be REGISTRAR_OFFICER
    or ADMIN at the auth layer (rejects pure students), AND the
    user must have a CourseManagementOfficer row with
    role=DEPARTMENT_HEAD. Plain registrar officers get 403.

    Once granted, the override is consulted by the
    CurriculumComplianceAgent on the next submit; the prereq check
    skips for the overridden course.
    """
    if current_user.role not in {UserRole.REGISTRAR_OFFICER, UserRole.ADMIN}:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only registrar officers or admins can grant prerequisite overrides.",
        )
    svc = RegistrationService(db)
    try:
        return await svc.grant_prerequisite_override(
            registration_id=registration_id,
            course_id=payload.course_id,
            officer_user_id=current_user.id,
            justification=payload.justification,
        )
    except UnauthorizedActorError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.detail)
    except InvalidAdjustmentRequestError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, exc.detail)
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


@router.post(
    "/officer/students/onboard-from-enrollment",
    response_model=StudentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def officer_onboard_student_from_enrollment(
    payload: StudentOnboardRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    email_service: EmailService = Depends(get_email_service),
):
    """
    Bridges an admission Enrollment row into a course-management
    Student row so the admitted student can use the Track A
    endpoints. Also issues portal credentials: replaces the user's
    password with a single-use 4-digit PIN, sets must_change_password
    on the User row, and emails the student their UGR ID + PIN.

    Officer-only (REGISTRAR_OFFICER or ADMIN). Idempotent:
    409 if a Student already exists for the Enrollment's user.
    """
    svc = OnboardingService(db, email_service=email_service)
    try:
        return await svc.onboard_student_from_enrollment(
            enrollment_id=payload.enrollment_id,
            officer_role=current_user.role,
            officer_id=current_user.id,
        )
    except UnauthorizedActorError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.detail)
    except StudentAlreadyOnboardedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


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


# ── Mock payment (mirrors /undergraduate/applications/.../payment/*) ─
#
# Two-step shape, identical to admission's mock:
#   1. Student calls /payment/initiate to mint a payment_reference
#      and get a (fake) gateway URL.
#   2. The "gateway" — i.e. you, in Swagger, until a real bursar is
#      wired up — POSTs that reference back to /payment/callback,
#      which marks every course in the draft as paid in the in-memory
#      PayMock. After the callback, /submit will clear the payment
#      check and reach REGISTERED.
#
# /initiate is student-only and ownership-checked; /callback is PUBLIC
# because in production it'd be hit by the bursar's webhook.


@router.post(
    "/registrations/{registration_id}/payment/initiate",
    response_model=RegistrationPaymentInitiateResponse,
)
async def initiate_registration_payment(
    registration_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Student initiates payment for their own draft registration.
    Returns a simulated gateway URL + the payment_reference that the
    callback will need to echo back. Idempotent: re-calling on a
    registration with an existing reference returns the same one.
    """
    student = await _resolve_student(db, current_user)
    svc = RegistrationService(db)
    try:
        registration = await svc.initiate_payment(
            registration_id, student_user_id=current_user.id,
        )
    except UnauthorizedActorError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.detail)
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    except InvalidStateTransitionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))

    # Defensive: should never happen since _resolve_student would have
    # raised, but keeps the type-checker happy.
    if registration.student_id != student.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your registration.")

    return RegistrationPaymentInitiateResponse(
        registration_id=registration.id,
        payment_reference=registration.payment_reference,
        payment_url=(
            f"https://pay.registrar.example.com/checkout/"
            f"{registration.payment_reference}"
        ),
    )


@router.post(
    "/registrations/{registration_id}/payment/callback",
    response_model=RegistrationResponse,
)
async def registration_payment_callback(
    registration_id: uuid.UUID,
    payload: RegistrationPaymentCallbackRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Simulated bursar-gateway callback. PUBLIC so it can be invoked
    without a JWT (mirrors admission's /payment/callback). Marks every
    course in the registration as paid in the in-memory PayMock.
    """
    svc = RegistrationService(db)
    try:
        return await svc.complete_payment(
            registration_id, payload.payment_reference,
        )
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    except InvalidStateTransitionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except InvalidAdjustmentRequestError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.detail)


@router.post(
    "/registrations/{registration_id}/cost-sharing-form",
    response_model=CostSharingFormResponse,
    summary="Submit the cost-sharing form (government-sponsored only)",
)
async def submit_cost_sharing_form(
    registration_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Government-sponsored students settle the term's tuition by
    signing a single cost-sharing form rather than paying course-
    by-course. Submitting the form marks every active course on the
    registration as paid in the bursar-mock and clears any active
    PAYMENT_HOLD so the student can /submit again.

    Self-sponsored students should use /payment/initiate +
    /payment/callback instead — calling this endpoint on a
    self-sponsored registration returns 409.
    """
    svc = RegistrationService(db)
    try:
        return await svc.submit_cost_sharing_form(
            registration_id=registration_id,
            student_user_id=current_user.id,
        )
    except UnauthorizedActorError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.detail)
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    except InvalidStateTransitionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except InvalidAdjustmentRequestError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, exc.detail)


# ── Instructor management (Department-Head endpoints) ──────────


@router.post(
    "/officer/instructors",
    response_model=InstructorResponse,
    status_code=status.HTTP_201_CREATED,
    summary="[Department Head] Create a new instructor",
)
async def officer_add_instructor(
    payload: InstructorCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    email_service: EmailService = Depends(get_email_service),
):
    """
    Create a new instructor profile + portal credentials. Generates a
    4-digit PIN, sets ``must_change_password=True``, and emails the
    instructor their staff_id + PIN. The PIN is never returned in the
    HTTP response.

    Department Head or ADMIN only.
    """
    svc = InstructorService(db, email_service=email_service)
    try:
        instructor, _pin = await svc.add_instructor(
            staff_id=payload.staff_id,
            email=payload.email,
            first_name=payload.first_name,
            last_name=payload.last_name,
            department=payload.department,
            officer_user_id=current_user.id,
        )
        return instructor
    except UnauthorizedActorError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.detail)
    except InvalidAdjustmentRequestError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, exc.detail)


@router.get(
    "/officer/instructors",
    response_model=list[InstructorResponse],
    summary="List instructors (filterable by department)",
)
async def officer_list_instructors(
    department: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    List instructors. Open to any logged-in user (officers,
    instructors browsing peers, etc.). Filter by department via
    query param to narrow the view.
    """
    del current_user  # auth confirmed; no role gate on read
    svc = InstructorService(db)
    return await svc.list_instructors(department=department)


@router.post(
    "/officer/instructor-assignments",
    response_model=InstructorAssignmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="[Department Head] Assign instructor to a course in a term",
)
async def officer_assign_instructor(
    payload: InstructorAssignmentCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Bind an instructor to a (course, term). Re-posting with a
    different instructor for the same (course, term) silently
    rebinds — at most one active assignment per (course, term).

    Department Head or ADMIN only.
    """
    svc = InstructorService(db)
    try:
        return await svc.assign_to_course(
            instructor_id=payload.instructor_id,
            course_id=payload.course_id,
            term_id=payload.term_id,
            officer_user_id=current_user.id,
        )
    except UnauthorizedActorError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.detail)
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


@router.get(
    "/officer/instructor-assignments",
    response_model=list[InstructorAssignmentResponse],
    summary="List instructor assignments (filter by term/course/instructor)",
)
async def officer_list_instructor_assignments(
    term_id: Optional[uuid.UUID] = None,
    course_id: Optional[uuid.UUID] = None,
    instructor_id: Optional[uuid.UUID] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List instructor assignments — readable by any logged-in user."""
    del current_user
    svc = InstructorService(db)
    return await svc.list_assignments(
        term_id=term_id,
        course_id=course_id,
        instructor_id=instructor_id,
    )


@router.delete(
    "/officer/instructor-assignments/{assignment_id}",
    status_code=204,
    summary="[Department Head] Remove an instructor assignment",
)
async def officer_unassign_instructor(
    assignment_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove an InstructorAssignment row. Department Head or ADMIN only."""
    svc = InstructorService(db)
    try:
        await svc.unassign(
            assignment_id=assignment_id,
            officer_user_id=current_user.id,
        )
    except UnauthorizedActorError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.detail)
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    return None
