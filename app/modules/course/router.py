
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm_client import LLMUnavailableError
from app.core.dependencies import get_current_user, get_email_service
from app.database.session import get_db
from app.modules.auth.models import User
from app.modules.course.models import (
    AdvisoryRecommendation, ClassScheduleSlot, CourseManagementOfficer,
    Instructor,
)
from app.modules.programs.models import AcademicProgram
from app.modules.course.exceptions import (
    ComplianceCheckFailedError,
    DuplicateRegistrationError,
    EntityNotFoundError,
    InvalidAdjustmentRequestError,
    InvalidStateTransitionError,
    RegistrationWindowClosedError,
    StudentAlreadyOnboardedError,
    TermNotYetOpenError,
    UnauthorizedActorError,
)
from app.modules.course.repository import StudentRepository
from app.modules.course.schemas import (
    AcademicTermResponse,
    AddDropBatchCreate,
    AddDropBatchResponse,
    AddDropPickerResponse,
    AddDropRequestResponse,
    AdvisoryConsultAddDropRequest,
    AdvisoryConsultResponse,
    AdvisoryEvaluateRequest,
    AdvisoryRecommendationRead,
    AdvisoryReviewCloseRequest,
    ConsultationRecommendedCourse,
    DepartmentTermOverviewResponse,
    GraduationImpactRead,
    AssignInstructorToSlotRequest,
    AvailableCoursesRequest,
    AvailableCoursesResponse,
    ComplianceResultResponse,
    CostSharingFormResponse,
    CourseResponse,
    InstructorAssignmentCreate,
    InstructorAssignmentResponse,
    InstructorCreateRequest,
    InstructorResponse,
    InstructorScheduleEntry,
    OfficerJustificationRequest,
    ScheduleAcceptRequest,
    ScheduleAcceptResponse,
    ScheduleOptionsResponse,
    RegistrationInvoiceResponse,
    RegistrationResponse,
    RegistrationSubmitResponse,
    SelectCoursesAndSubmitRequest,
    ScheduleConflictRead,
    ScheduleGenerateRequest,
    SectionAllocationResponse,
    SlotInstructorAssignmentResponse,
    TimetableGenerateResponse,
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
from app.shared.enums import AddDropBatchStatus, OfficerRole, UserRole


router = APIRouter(prefix="/courses", tags=["Course Management"])


# ── Term catalog (open to every authenticated user) ──────────────


@router.get(
    "/terms",
    response_model=list[AcademicTermResponse],
    summary="List academic terms — open and closed, ordered by start_date",
)
async def list_terms(
    is_open: Optional[bool] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Catalog of every (non-deleted) AcademicTerm. Visible to officers,
    instructors, and students — every role needs a term picker.

    The optional ``is_open`` query param narrows the result:

      * ``is_open=true``  → only the currently-open registration windows
      * ``is_open=false`` → only closed / future terms
      * omitted           → every term

    Ordered by ``start_date`` ascending so the calendar reads
    chronologically.
    """
    del current_user  # auth confirmed; no role gate
    svc = TermService(db)
    return await svc.list_terms(is_open=is_open)


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
    "/me/available-courses",
    response_model=AvailableCoursesResponse,
    summary="Courses available to the calling student for a given term",
)
async def list_my_available_courses(
    payload: AvailableCoursesRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Term-keyed view, resolved by three rules:

      1. Term is OPEN. The lookup always checks for an existing
         Registration so the frontend can render the correct action
         button after the student submits and returns to this page:
           * Registration exists → 200 with
             ``is_registered=true``, ``registration_status`` (one of
             ``REGISTRATION_OPEN`` / ``PAYMENT_HOLD`` / ``REGISTERED``
             / …), and ``courses`` = active registered courses.
           * No Registration → 200 with ``is_registered=false`` and
             ``courses`` = curriculum picker (department + per-term
             semester filter).
      2. Term is CLOSED and has already started.
           * Has a Registration → 200 with the student's active
             (non-dropped) registered courses, plus ``registration_id``
             + ``registration_status``.
           * No Registration → 200 with ``is_registered=false`` and
             ``courses=[]``. Not an error — the frontend renders the
             "you weren't registered for this term" empty state.
      3. Term is CLOSED and has not started yet → 200 with
         ``is_registered=false`` and ``courses=[]``. Same shape as the
         past-term no-registration case; the frontend renders a "this
         term hasn't opened yet" empty-state card.

    Returns 404 when ``term_id`` does not match any existing
    (non-deleted) AcademicTerm, or when the calling student row is
    missing.
    """
    student = await _resolve_student(db, current_user)
    svc = RegistrationService(db)
    try:
        return await svc.list_available_courses(student.id, payload.term_id)
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


@router.post(
    "/me/register",
    response_model=RegistrationSubmitResponse,
    summary="Pick courses and register in one call (collapsed flow)",
)
async def register_me(
    payload: SelectCoursesAndSubmitRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Single-shot registration: the student picks the term and the
    exact set of courses they want to take, and the service does
    every intermediate step (create draft → sync course list →
    submit + run compliance) in one call.

    Idempotent on retry while the registration is in
    ``REGISTRATION_OPEN``: if the previous submit bounced (prereq
    miss, payment hold) the student can fix the input and POST again.
    """
    student = await _resolve_student(db, current_user)
    svc = RegistrationService(db)
    try:
        registration, compliance = await svc.select_courses_and_submit(
            student_id=student.id,
            student_user_id=current_user.id,
            term_id=payload.term_id,
            course_ids=payload.course_ids,
        )
    except RegistrationWindowClosedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except DuplicateRegistrationError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except InvalidStateTransitionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    except ComplianceCheckFailedError as exc:
        # Stitch the agent's per-check reasons into one human-readable
        # string. The exception carries the full structured payload on
        # ``exc.payload`` (prereq_results / load_result / payment_result),
        # each with a ``reasons`` list of plain-language strings.
        reasons: list[str] = []
        payload = exc.payload or {}
        for prereq in payload.get("prereq_results", []) or []:
            if not prereq.get("passed", True):
                reasons.extend(prereq.get("reasons", []) or [])
        for key in ("load_result", "payment_result"):
            result = payload.get(key) or {}
            if not result.get("passed", True):
                reasons.extend(result.get("reasons", []) or [])
        # Use "\n" as the separator — individual reason strings can
        # contain semicolons (e.g. "Payment outstanding for 10 course(s);
        # registration cannot be finalised until settled."), so the
        # frontend needs an unambiguous splitter to render bullets.
        detail = "\n".join(r for r in reasons if r) or str(exc)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail)
    return RegistrationSubmitResponse(
        registration=registration, compliance=compliance,
    )


@router.get(
    "/registrations/{registration_id}",
    response_model=RegistrationResponse,
)
async def get_registration(
    registration_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Read view of the calling student's own registration — the spine
    of the "this semester" portal view.

    Returns:
      * core fields (status, sponsorship_type, finalised_at, …)
      * ``term_name`` so the caller does not need a separate term
        lookup
      * ``courses`` enriched with each ``course`` row inline (code,
        title, credit_hours, semester, department) so the portal can
        render the registration without a second curriculum call
      * computed aggregates: ``active_courses`` (subset that excludes
        dropped rows), ``active_credit_total``, ``active_course_count``

    For writes, students should use the unified
    ``POST /me/register`` — the previous granular endpoints
    (create-draft / add-course / remove-course / submit) were
    consolidated into that one call.
    """
    student = await _resolve_student(db, current_user)
    svc = RegistrationService(db)
    registration = await svc.registrations.get(registration_id)
    if registration is None or registration.student_id != student.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Registration not found.")
    # ``term`` is selectin-loaded on the model relationship, so this
    # does not fire an extra query. Build the response off the ORM
    # row, then patch ``term_name`` (Pydantic's from_attributes can't
    # reach term.term_name through a relationship without a custom
    # adapter).
    response = RegistrationResponse.model_validate(
        registration, from_attributes=True,
    )
    if registration.term is not None:
        response = response.model_copy(
            update={"term_name": registration.term.term_name},
        )
    return response


# ── Scheduling endpoints ─────────────────────────────────────────


async def _resolve_program_department(
    db: AsyncSession, program_id: uuid.UUID,
) -> str:
    """
    Resolve an ``AcademicProgram`` UUID to its ``department`` name —
    the string the scheduling tables key off. 404 if the program does
    not exist or has been soft-deleted.
    """
    program = (
        await db.execute(
            select(AcademicProgram).where(
                AcademicProgram.id == program_id,
                AcademicProgram.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if program is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"AcademicProgram with id {program_id} not found.",
        )
    return program.department


async def _resolve_scheduling_department(
    db: AsyncSession,
    current_user: User,
    program_id: uuid.UUID | None,
) -> str:
    """
    Resolve the department a scheduling action should run against.

    Department Heads operate on their own department: when
    ``program_id`` is omitted, the department is read from the
    caller's ``CourseManagementOfficer.department``. Admins (who can
    operate across departments) must pass ``program_id`` to pick a
    target. If ``program_id`` is provided, it wins for both roles —
    the service-layer auth check still enforces that a DH can only
    target their own department.
    """
    if program_id is not None:
        return await _resolve_program_department(db, program_id)

    if current_user.role == UserRole.ADMIN:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Admins must pass program_id to choose a department.",
        )

    officer = (
        await db.execute(
            select(CourseManagementOfficer).where(
                CourseManagementOfficer.user_id == current_user.id,
                CourseManagementOfficer.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if officer is None or officer.role != OfficerRole.DEPARTMENT_HEAD:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only a Department Head (or admin) may run scheduling.",
        )
    if officer.department is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Department Head '{officer.staff_id}' has no department "
            "assigned — cannot run scheduling. Contact an administrator.",
        )
    return officer.department


@router.post(
    "/officer/sections/allocate",
    response_model=SectionAllocationResponse,
    summary="Phase 1 — allocate REGISTERED students into cohort sections",
)
async def officer_allocate_sections(
    payload: ScheduleGenerateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Department-Head-only (admins also allowed): group every REGISTERED
    student in this department (across semesters 1–10) into cohort
    sections sized to the department's :class:`Classroom` inventory.
    Every ``Registration.section_id`` in the department gets pinned.

    Does *not* emit weekly class meetings — that's the separate
    :func:`officer_generate_timetable` call below, which the DH
    runs after reviewing the cohort split.

    Idempotent on re-runs: students already pinned stay put; new
    students fill remaining capacity before fresh sections are
    created.
    """
    department = await _resolve_scheduling_department(
        db, current_user, payload.program_id,
    )
    svc = SchedulingService(db)
    try:
        result = await svc.allocate_sections(
            term_id=payload.term_id,
            department=department,
            officer_user_id=current_user.id,
        )
    except UnauthorizedActorError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.detail)
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    except InvalidAdjustmentRequestError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, exc.detail,
        )
    return SectionAllocationResponse(**result)


@router.post(
    "/officer/schedule/generate",
    response_model=TimetableGenerateResponse,
    summary="Phase 2 — generate weekly class meetings for each section",
)
async def officer_generate_timetable(
    payload: ScheduleGenerateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Department-Head-only (admins also allowed): build the per-section
    weekly schedule (``ClassScheduleSlot`` rows) for every Section
    the department has. Total weekly hours per course ==
    ``course.credit_hours``.

    Prerequisite: :func:`officer_allocate_sections` must have run for
    this (term, department) — if no sections exist yet, the response
    is empty (no slots created).

    Idempotent: re-runs delete the department's existing slots and
    rebuild from scratch.
    """
    department = await _resolve_scheduling_department(
        db, current_user, payload.program_id,
    )
    svc = SchedulingService(db)
    try:
        result = await svc.generate_timetable(
            term_id=payload.term_id,
            department=department,
            officer_user_id=current_user.id,
        )
    except UnauthorizedActorError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.detail)
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    except InvalidAdjustmentRequestError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, exc.detail,
        )
    return TimetableGenerateResponse(**result)


@router.get(
    "/officer/sections/overview",
    response_model=DepartmentTermOverviewResponse,
    summary="Read-only view of what scheduling has produced for the caller's department",
)
async def officer_department_term_overview(
    term_id: uuid.UUID,
    program_id: uuid.UUID | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns existing sections + schedule status for a (term,
    department) without mutating anything. Department Heads omit
    ``program_id`` — their department is read from their
    ``CourseManagementOfficer`` row. Admins must pass ``program_id``.

    The frontend uses ``has_sections`` / ``has_slots`` to gate the
    allocate / generate buttons, and ``sections`` to render the
    previously generated cohort split without a re-run.
    """
    department = await _resolve_scheduling_department(
        db, current_user, program_id,
    )
    svc = SchedulingService(db)
    try:
        result = await svc.get_department_term_overview(
            term_id=term_id,
            department=department,
            officer_user_id=current_user.id,
        )
    except UnauthorizedActorError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.detail)
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    return DepartmentTermOverviewResponse(**result)


@router.get(
    "/officer/schedule/conflicts",
    response_model=list[ScheduleConflictRead],
)
async def officer_list_conflicts(
    term_id: uuid.UUID,
    program_id: uuid.UUID | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    department: str | None = None
    if program_id is not None:
        department = await _resolve_program_department(db, program_id)
    svc = SchedulingService(db)
    try:
        rows = await svc.list_open_conflicts(
            term_id=term_id,
            officer_user_id=current_user.id,
            department=department,
        )
    except UnauthorizedActorError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.detail)
    return rows


@router.post(
    "/officer/schedule/slots/{slot_id}/assign-instructor",
    response_model=SlotInstructorAssignmentResponse,
    summary="Reassign the instructor pinned to a single schedule slot",
)
async def officer_assign_slot_instructor(
    slot_id: uuid.UUID,
    payload: AssignInstructorToSlotRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Department-Head-only (admins also allowed): replace the
    instructor on a single ``ClassScheduleSlot``. Refuses with 422
    if the new instructor is already booked elsewhere in the term
    at the same ``(day_of_week, start_time)``.
    """
    svc = SchedulingService(db)
    try:
        slot = await svc.assign_instructor_to_slot(
            slot_id=slot_id,
            instructor_id=payload.instructor_id,
            officer_user_id=current_user.id,
        )
    except UnauthorizedActorError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.detail)
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    except InvalidAdjustmentRequestError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, exc.detail,
        )
    return SlotInstructorAssignmentResponse(
        slot_id=slot.id,
        section_id=slot.section_id,
        course_id=slot.course_id,
        instructor_id=slot.instructor_id,
        day_of_week=slot.day_of_week,
        start_time=slot.start_time.isoformat(timespec="minutes"),
        end_time=slot.end_time.isoformat(timespec="minutes"),
    )


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


# ── Per-student schedule deltas (post-add/drop section choice) ──
#
# The add/drop apply path automatically removes dropped courses
# from the schedule (cohort slots are filtered out at read time;
# any per-student additions for the dropped course are deleted).
# Added courses, however, may be offered in multiple sections and
# the student needs to pick one — these endpoints walk that flow:
#
#   GET  /me/schedule/options-for/{course_id}
#        AcademicSchedulingAgent.propose_options_for_course →
#        every Section that offers the course, each with a
#        conflict flag against the student's current schedule.
#
#   POST /me/schedule/accept-for/{course_id}
#        Materialises the chosen section's slots as
#        StudentScheduleAddition rows. Re-checks conflicts so a
#        stale client cannot bypass.


@router.get(
    "/me/schedule/options-for/{course_id}",
    response_model=ScheduleOptionsResponse,
    summary="Section options for an added course (with conflict flags)",
)
async def get_schedule_options_for_course(
    course_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    For an active course on the calling student's registration,
    list every section that runs it. Each option carries the
    candidate slot package and a ``conflicts`` list (empty when
    ``is_viable=true``) describing any collisions with the
    student's current effective schedule.
    """
    student = await _resolve_student(db, current_user)
    svc = SchedulingService(db)
    try:
        return await svc.propose_options_for_added_course(
            student_id=student.id, course_id=course_id,
        )
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


@router.post(
    "/me/schedule/accept-for/{course_id}",
    response_model=ScheduleAcceptResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Pick a section for an added course; integrate into schedule",
)
async def accept_section_for_added_course(
    course_id: uuid.UUID,
    payload: ScheduleAcceptRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Apply the student's chosen section: writes one
    ``StudentScheduleAddition`` row per slot the section runs for
    this course. Idempotent on retry. Returns 409 when the chosen
    section would conflict with the current schedule (must pick a
    section flagged ``is_viable=true`` from the options endpoint).
    """
    student = await _resolve_student(db, current_user)
    svc = SchedulingService(db)
    try:
        return await svc.accept_section_for_added_course(
            student_id=student.id,
            course_id=course_id,
            section_id=payload.section_id,
        )
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    except InvalidAdjustmentRequestError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, exc.detail)


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
    "/sections/{section_id}/schedule",
    response_model=SectionScheduleResponse,
    summary="Weekly schedule for a section",
    response_description=(
        "Section metadata + every ClassScheduleSlot on the section, "
        "sorted by day_of_week then start_time."
    ),
)
async def get_section_schedule_by_id(
    section_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Officer/admin lookup of a cohort section's weekly timetable.

    **Auth:** `REGISTRAR_OFFICER` or `ADMIN` only — anyone else gets
    `403`. Use `GET /me/schedule` (student) or
    `GET /instructors/me/schedule` (instructor) for self-views.

    **Path params:** `section_id` — the cohort section.

    **Response shape** (`SectionScheduleResponse`):
    - `term_id` — the section's academic term.
    - `student_id` — always `null` on this endpoint.
    - `section` — `{section_id, section_code, department, semester,
      capacity, enrolled_count}`.
    - `slots[]` — each item: `course_code`, `course_title`,
      `day_of_week` (MON–FRI), `start_time` / `end_time` (`HH:MM`),
      `instructor_id` (nullable when unassigned), `room` (per-slot —
      a cohort may meet in different rooms during the week).
    - `pending_additions[]` — always empty here; only populated by
      `/me/schedule`.

    An empty `slots[]` means the scheduling agent has not run for
    this section's department yet — call
    `POST /officer/schedule/generate` to build the timetable.

    **Errors:**
    - `401` — missing / expired JWT.
    - `403` — caller is not officer/admin.
    - `404` — section does not exist (or is soft-deleted).
    - `422` — malformed `section_id` UUID.
    """
    if current_user.role not in {UserRole.REGISTRAR_OFFICER, UserRole.ADMIN}:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only registrar officers or admins may look up section schedules.",
        )
    svc = SchedulingService(db)
    try:
        return await svc.get_section_schedule(section_id)
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


@router.get(
    "/sections/{section_id}/students",
    response_model=list[StudentResponse],
    summary="Roster of students allocated to a section",
    response_description=(
        "List of Student rows whose Registration.section_id matches, "
        "sorted by student_id ascending."
    ),
)
async def list_section_students(
    section_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Officer/admin roster lookup. Resolves the cohort by joining
    `students` to non-deleted `registrations` where
    `registration.section_id = {section_id}` — so the result reflects
    the AcademicSchedulingAgent's current allocation. Students who
    were moved to a different section by add/drop drop off this list
    automatically.

    **Auth:** `REGISTRAR_OFFICER` or `ADMIN` only — anyone else gets
    `403`. Instructors should use the per-course roster at
    `GET /courses/grading/sections/{section_id}/courses/{course_id}/roster`
    instead, which is scoped to the courses they teach.

    **Path params:** `section_id` — the cohort section.

    **Response shape** — `list[StudentResponse]`. Each row:
    - `id` — `students.id` (course-management PK).
    - `user_id` — `users.id` (auth identity).
    - `student_id` — UGR id string (e.g. `"UGR/0001/14"`).
    - `full_name` — display name.
    - `current_semester` — 1–10.
    - `enrollment_status` — `ACTIVE` | `SUSPENDED` | `GRADUATED`
      | `WITHDRAWN`.

    An empty list means the agent has not allocated anyone to this
    section yet — call `POST /officer/sections/allocate` first.

    **Errors:**
    - `401` — missing / expired JWT.
    - `403` — caller is not officer/admin.
    - `404` — section does not exist (or is soft-deleted).
    - `422` — malformed `section_id` UUID.
    """
    if current_user.role not in {UserRole.REGISTRAR_OFFICER, UserRole.ADMIN}:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only registrar officers or admins may look up section students.",
        )
    svc = SchedulingService(db)
    try:
        return await svc.list_section_students(section_id)
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


@router.get(
    "/instructors/me/schedule",
    response_model=list[InstructorScheduleEntry],
    summary="Calling instructor's own weekly schedule",
)
async def get_my_instructor_schedule(
    term_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Instructor-only convenience read: every ``ClassScheduleSlot``
    where the calling user is the assigned instructor in ``term_id``.
    Resolves the Instructor row from the JWT's ``user_id`` and
    forwards to the shared :func:`get_instructor_schedule`.

    Returns 403 if the caller is not an INSTRUCTOR or has no
    Instructor profile.
    """
    if current_user.role != UserRole.INSTRUCTOR:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only instructors may view their own teaching schedule.",
        )
    instructor = (
        await db.execute(
            select(Instructor).where(
                Instructor.user_id == current_user.id,
                Instructor.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if instructor is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Calling user has no instructor profile.",
        )
    svc = SchedulingService(db)
    return await svc.get_instructor_schedule(instructor.id, term_id)


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
#
# The student submits one batch carrying multiple (course, action)
# items. The EnrollmentAdjustmentAgent reviews the batch as a whole
# (curriculum, semester parity, prereqs, credit envelope, payment)
# and stamps an AGENT_APPROVED or AGENT_DENIED verdict on the batch
# row. Nothing is applied at this point — the batch then awaits an
# officer decision.
#
# Officer endpoints:
#   GET  /officer/add-drop/batches            — queue (AGENT_*)
#   POST /officer/add-drop/batches/{id}/approve   — approve agent-approved batch
#   POST /officer/add-drop/batches/{id}/override  — override agent-denied batch
#   POST /officer/add-drop/batches/{id}/reject    — finalise denial


@router.post(
    "/add-drop/batches",
    response_model=AddDropBatchResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_add_drop_batch(
    payload: AddDropBatchCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    email_service: EmailService = Depends(get_email_service),
):
    """
    Student-facing batch submission. Carries one or more
    (course_id, action) items. The agent reviews against:
      - curriculum membership (course.department == student.department)
      - semester parity (course offered this half of the year)
      - prerequisites (via CurriculumComplianceAgent + Grade ledger)
      - credit-load envelope (12–22 ECTS across the batch)
      - per-item payment cross-check for ADD

    The batch lands in AGENT_APPROVED or AGENT_DENIED and waits for
    an officer; no changes are applied to the registration yet.
    """
    student = await _resolve_student(db, current_user)
    svc = AddDropService(db, email_service=email_service)

    registration = await svc.registrations.get(payload.registration_id)
    if registration is None or registration.student_id != student.id:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Registration not found."
        )

    try:
        return await svc.submit_batch(
            registration_id=payload.registration_id,
            items=[(it.course_id, it.action) for it in payload.items],
            student_user_id=current_user.id,
            deadline=payload.deadline,
        )
    except InvalidAdjustmentRequestError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, exc.detail)
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


@router.get(
    "/add-drop/batches/{batch_id}",
    response_model=AddDropBatchResponse,
)
async def get_add_drop_batch(
    batch_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Owner-or-officer read of one batch + its items + verdicts."""
    svc = AddDropService(db)
    batch = await svc.get_batch(batch_id)
    if batch is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Add/drop batch not found."
        )
    if current_user.role not in {UserRole.REGISTRAR_OFFICER, UserRole.ADMIN}:
        student = await _resolve_student(db, current_user)
        if batch.student_id != student.id:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "Add/drop batch not found."
            )
    return batch


@router.get(
    "/me/add-drop/batches",
    response_model=list[AddDropBatchResponse],
)
async def list_my_add_drop_batches(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Student view of their own batch history (newest first)."""
    student = await _resolve_student(db, current_user)
    svc = AddDropService(db)
    return await svc.list_batches_for_student(student.id)


@router.get(
    "/me/add-drop/picker",
    response_model=AddDropPickerResponse,
    summary="Add/drop picker snapshot for the calling student",
    response_description=(
        "Current open-term registration id + three lists: droppable "
        "(active_courses), re-addable (dropped_courses), and "
        "first-time-addable catalog courses for the student's "
        "department (catalog_courses). Items with an in-flight DROP "
        "or ADD request are tagged pending_drop / pending_add."
    ),
)
async def get_my_add_drop_picker(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Single round trip for the add/drop UI. Resolves the calling
    student's registration for the currently-open AcademicTerm and
    returns the registration id plus both action buckets the picker
    needs to render.

    **Auth:** any authenticated student. The endpoint always operates
    on the caller's own registration — there is no student-id
    parameter.

    **Response shape** (`AddDropPickerResponse`):
    - `registration_id` — pass this verbatim to
      `POST /add-drop/batches` when submitting changes.
    - `term_id`, `term_name` — the open AcademicTerm.
    - `registration_status` — `REGISTRATION_OPEN` | `REGISTERED` |
      `ADD_DROP_WINDOW` | `CANCELLED`.
    - `active_courses[]` — registration links where
      `is_dropped=false`. **Candidates for DROP.** Each item also
      carries `pending_drop: bool` — `true` when a DROP request for
      the same course exists on a non-terminal batch
      (`PENDING_AGENT` / `AGENT_APPROVED` / `AGENT_DENIED`). The UI
      should render those as "awaiting approval" rather than offering
      another DROP button to avoid duplicate submissions.
    - `dropped_courses[]` — registration links where
      `is_dropped=true`. **Candidates for re-ADD.** Each item also
      carries `pending_add: bool` — `true` when an ADD request for
      the same course exists on a non-terminal batch
      (`PENDING_AGENT` / `AGENT_APPROVED` / `AGENT_DENIED`). The UI
      should render those as "awaiting approval" rather than offering
      another ADD button to avoid duplicate submissions.
    - `catalog_courses[]` — department-matching `Course` rows the
      student has **not** registered for (in any state) and has not
      previously completed with a passing grade. **Candidates for a
      first-time ADD**, covering past / current / future curriculum
      semesters. Each item is a `CatalogAddableRead` (`course_id`,
      `pending_add`, `course`) — no registration-link id because no
      `registration_courses` row exists yet. `pending_add` mirrors
      the in-flight semantics above.

    `active_courses` and `dropped_courses` items are
    `RegistrationCourseRead`: `id` (the `registration_courses.id`),
    `course_id`, `section_id` (nullable until allocation),
    `is_dropped`, `pending_drop`, `pending_add`, and `course` — the
    nested `CourseResponse` (`code`, `title`, `credit_hours`,
    `semester`, `department`).

    **Submitting an ADD or DROP:** pass `registration_id` and a list
    of `{course_id, action}` items to
    `POST /add-drop/batches`. The agent runs curriculum / semester
    parity / prereqs / credit-envelope checks and parks the batch
    at `AGENT_APPROVED` or `AGENT_DENIED` — **the registration is
    not mutated** until an officer approves (or overrides a denial)
    via `POST /officer/add-drop/batches/{batch_id}/approve` or
    `/override`.

    **Errors:**
    - `401` — missing / expired JWT.
    - `403` — caller has no student profile.
    - `404` — no AcademicTerm has `is_open=true`, or the student has
      no `registrations` row for the open term (hit
      `POST /me/register` first).
    """
    student = await _resolve_student(db, current_user)
    svc = RegistrationService(db)
    try:
        return await svc.get_add_drop_picker(student.id)
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


@router.get(
    "/officer/add-drop/batches",
    response_model=list[AddDropBatchResponse],
)
async def list_officer_add_drop_batches(
    status_filter: Optional[AddDropBatchStatus] = Query(
        default=None,
        alias="status",
        description=(
            "Narrow the queue to one workflow status. Must be "
            "AGENT_APPROVED or AGENT_DENIED. Omit to see both "
            "(the default queue)."
        ),
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Officer queue. Defaults to batches awaiting a human decision —
    status in {AGENT_APPROVED, AGENT_DENIED}. Pass ``?status=...``
    to fetch only one. Officers see every student's pending batch;
    sorted oldest-first so the queue drains in submission order.
    """
    if status_filter is not None and status_filter not in {
        AddDropBatchStatus.AGENT_APPROVED,
        AddDropBatchStatus.AGENT_DENIED,
    }:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "status filter must be AGENT_APPROVED or AGENT_DENIED — "
            "the queue surfaces only batches awaiting an officer decision.",
        )
    svc = AddDropService(db)
    try:
        return await svc.list_pending_batches(
            officer_role=current_user.role,
            statuses={status_filter} if status_filter else None,
        )
    except UnauthorizedActorError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.detail)


@router.post(
    "/officer/add-drop/batches/{batch_id}/approve",
    response_model=AddDropBatchResponse,
)
async def officer_approve_add_drop_batch(
    batch_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    email_service: EmailService = Depends(get_email_service),
):
    """
    Officer approves an AGENT_APPROVED batch — every item is
    materialised against the registration and the batch transitions
    to APPLIED.
    """
    svc = AddDropService(db, email_service=email_service)
    try:
        return await svc.officer_approve_batch(
            batch_id,
            officer_role=current_user.role,
            officer_id=current_user.id,
        )
    except UnauthorizedActorError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.detail)
    except InvalidAdjustmentRequestError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, exc.detail)
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


@router.post(
    "/officer/add-drop/batches/{batch_id}/override",
    response_model=AddDropBatchResponse,
)
async def officer_override_add_drop_batch(
    batch_id: uuid.UUID,
    payload: OfficerJustificationRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    email_service: EmailService = Depends(get_email_service),
):
    """
    Officer overrides an AGENT_DENIED batch — applies every item
    against the registration despite the agent's denial.
    Justification is required (officer is going against the agent).
    """
    svc = AddDropService(db, email_service=email_service)
    try:
        return await svc.officer_override_batch(
            batch_id,
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


@router.post(
    "/officer/add-drop/batches/{batch_id}/reject",
    response_model=AddDropBatchResponse,
)
async def officer_reject_add_drop_batch(
    batch_id: uuid.UUID,
    payload: OfficerJustificationRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Officer finalises the denial — no changes are applied.
    Works on either AGENT_APPROVED (officer disagrees with agent)
    or AGENT_DENIED (officer confirms agent). Justification required.
    """
    svc = AddDropService(db)
    try:
        return await svc.officer_reject_batch(
            batch_id,
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


# ── Demand-driven LLM consultations ──────────────────────────────
#
# Three student-initiated entry points to the AcademicAdvisoryAgent's
# Gemini-backed consult flow. Hard-fail contract: if the LLM cannot
# answer (no GEMINI_API_KEY, timeout, API error, malformed JSON), we
# return 503 ServiceUnavailable rather than silently degrading to
# the rule engine — these endpoints exist precisely to deliver the
# deep graduation-trajectory analysis the rule engine cannot.


def _to_consult_response(
    rec: AdvisoryRecommendation,
) -> AdvisoryConsultResponse:
    """
    Project a persisted AdvisoryRecommendation row into the consult
    response shape. Reads the LLM-specific fields (graduation_impact,
    consultation_mode, the recommended_courses LLM-shape) and surfaces
    the warnings + filtered_recommendations that the agent stuffed
    into ``gap_analysis`` for the consult flow.
    """
    gap = rec.gap_analysis or {}
    grad = rec.graduation_impact or {}
    return AdvisoryConsultResponse(
        recommendation_id=rec.id,
        student_id=rec.student_id,
        term_id=rec.term_id,
        mode=rec.consultation_mode,
        verdict=str(gap.get("verdict") or "NEEDS_REVIEW"),
        risk_status=rec.risk_status,
        narrative=rec.risk_explanation,
        recommended_courses=[
            ConsultationRecommendedCourse(
                course_code=str(item.get("course_code", "")),
                title=str(item.get("title", "")),
                credit_hours=int(item.get("credit_hours") or 0),
                is_core=bool(item.get("is_core", False)),
                reason=str(item.get("reason", "")),
                requires_override=item.get("requires_override"),
            )
            for item in (rec.recommended_courses or [])
            if isinstance(item, dict)
        ],
        warnings=[str(w) for w in (gap.get("warnings") or [])],
        graduation_impact=GraduationImpactRead(
            semesters_remaining=grad.get("semesters_remaining"),
            on_track=grad.get("on_track"),
            expected_graduation_semester=grad.get(
                "expected_graduation_semester"
            ),
            delay_semesters=grad.get("delay_semesters"),
            critical_path_courses=list(
                grad.get("critical_path_courses") or []
            ),
        ),
        filtered_recommendations=list(
            gap.get("filtered_recommendations") or []
        ),
        created_at=rec.created_at,
    )


@router.post(
    "/advisory/consult/pre-registration",
    response_model=AdvisoryConsultResponse,
    status_code=status.HTTP_201_CREATED,
)
async def consult_pre_registration(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    "What should I take this semester?" — no body needed. Server
    reads the student's profile, the open AcademicTerm, the student's
    completed courses + CGPA from the Grade ledger, and the
    department curriculum; the LLM returns a recommended plan +
    graduation-trajectory analysis.
    """
    student = await _resolve_student(db, current_user)
    svc = AdvisoryService(db)
    try:
        rec = await svc.consult_pre_registration(student_id=student.id)
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    except LLMUnavailableError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"Advisory LLM unavailable: {exc.reason}",
        )
    return _to_consult_response(rec)


@router.post(
    "/advisory/consult/registration-plan",
    response_model=AdvisoryConsultResponse,
    status_code=status.HTTP_201_CREATED,
)
async def consult_registration_plan(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    "Is my current draft sound?" — no body needed. Server reads the
    student's in-progress Registration for the open term and
    validates the active course set against curriculum + history.
    Returns 404 when the student has no draft (they should hit
    /advisory/consult/pre-registration instead).
    """
    student = await _resolve_student(db, current_user)
    svc = AdvisoryService(db)
    try:
        rec = await svc.consult_registration_plan(student_id=student.id)
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    except LLMUnavailableError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"Advisory LLM unavailable: {exc.reason}",
        )
    return _to_consult_response(rec)


@router.post(
    "/advisory/consult/add-drop",
    response_model=AdvisoryConsultResponse,
    status_code=status.HTTP_201_CREATED,
)
async def consult_add_drop(
    payload: AdvisoryConsultAddDropRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Two operating modes:

      - **Guided** ("if I add X / drop Y, am I still on track?") —
        body carries one or both of ``add_course_ids`` and
        ``drop_course_ids``; the agent evaluates that specific
        hypothetical against the current registration.
      - **Proactive** ("what, if anything, should I change?") —
        body is empty (or both lists empty); the agent reviews the
        active registration against the curriculum + history and
        either recommends concrete adds / drops with reasons, or
        confirms the plan is already healthy.

    In both modes the server finds the student's REGISTERED /
    ADD_DROP_WINDOW registration in the open term. Returns 404 when
    the student has no active registration.
    """
    student = await _resolve_student(db, current_user)
    svc = AdvisoryService(db)
    try:
        rec = await svc.consult_add_drop(
            student_id=student.id,
            add_course_ids=payload.add_course_ids,
            drop_course_ids=payload.drop_course_ids,
        )
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    except LLMUnavailableError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"Advisory LLM unavailable: {exc.reason}",
        )
    return _to_consult_response(rec)


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


@router.get(
    "/registrations/{registration_id}/invoice",
    response_model=RegistrationInvoiceResponse,
    summary="Tuition invoice — per-credit-hour breakdown",
)
async def get_registration_invoice(
    registration_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Per-credit-hour tuition breakdown for the calling student's own
    registration. Each non-dropped course contributes ``credit_hours
    * FEE_PER_CREDIT_HOUR_BIRR`` (default 100 birr / credit hour).

    Self-sponsored students see ``amount_due`` = the full sum; they
    pay it via /payment/initiate + /payment/callback. Government-
    sponsored students see the same line items but ``amount_due=0``
    — cost-sharing covers it via /cost-sharing-form.

    Reachable in any registration status so the student can review
    the bill from draft, after a PAYMENT_HOLD bounce, and after
    REGISTERED for their records.
    """
    svc = RegistrationService(db)
    try:
        return await svc.get_invoice(
            registration_id=registration_id,
            student_user_id=current_user.id,
        )
    except UnauthorizedActorError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.detail)
    except EntityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


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
