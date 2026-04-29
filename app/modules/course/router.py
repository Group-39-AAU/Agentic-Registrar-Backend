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

from app.core.dependencies import get_current_user
from app.database.session import get_db
from app.modules.auth.models import User
from app.modules.course.exceptions import (
    ComplianceCheckFailedError,
    DuplicateRegistrationError,
    EntityNotFoundError,
    InvalidStateTransitionError,
    RegistrationWindowClosedError,
    UnauthorizedActorError,
)
from app.modules.course.repository import StudentRepository
from app.modules.course.schemas import (
    AcademicTermResponse,
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
    RegistrationService, SchedulingService, TermService,
)
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
