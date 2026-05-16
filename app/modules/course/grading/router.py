"""
Track B (Grading) — FastAPI routes.

Mounted from ``app.modules.course.router`` so every grading route sits
under the existing ``/courses`` prefix. PR 1 ships two reads:

    GET  /courses/grading/me/sections?term_id={uuid}
    GET  /courses/grading/sections/{section_id}/courses/{course_id}/roster

Both are instructor-only; ownership of the (section, course) pair is
enforced inside the service layer, not in the route handler, so the
same rules apply if other paths reuse the service later.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.database.session import get_db
from app.modules.auth.models import User
from app.modules.course.exceptions import (
    EntityNotFoundError, UnauthorizedActorError,
)
from app.modules.course.grading.schemas import (
    InstructorSectionAssignmentResponse, SectionCourseRosterResponse,
)
from app.modules.course.grading.service import InstructorGradingService
from app.shared.enums import UserRole


# Nested under the same /courses prefix as the rest of the module so
# the OpenAPI tree groups grading with course management.
router = APIRouter(prefix="/courses/grading", tags=["Course Management — Grading"])


def _require_instructor(current_user: User) -> None:
    """Reject non-instructor callers with a uniform 403."""
    if current_user.role != UserRole.INSTRUCTOR:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only instructors may access the grading instructor surface.",
        )


# ── Endpoint 1 — "which (section, course) pairs do I teach?" ──


@router.get(
    "/me/sections",
    response_model=list[InstructorSectionAssignmentResponse],
    summary="Sections × courses the calling instructor teaches in a term",
)
async def list_my_section_assignments(
    term_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns one entry per (section, course) pair the calling
    instructor is assigned to teach in ``term_id``. The grading unit
    is the pair — a cohort section serves multiple courses and each
    one is graded independently by its assigned instructor.

    Empty list (200) if the instructor teaches nothing this term.
    403 if the caller is not an INSTRUCTOR or has no Instructor row.
    """
    _require_instructor(current_user)
    svc = InstructorGradingService(db)
    try:
        return await svc.list_my_section_assignments(
            user_id=current_user.id, term_id=term_id,
        )
    except UnauthorizedActorError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.detail) from exc


# ── Endpoint 2 — "roster of (section, course) I teach" ──


@router.get(
    "/sections/{section_id}/courses/{course_id}/roster",
    response_model=SectionCourseRosterResponse,
    summary="Effective roster for a (section, course) the caller teaches",
)
async def get_section_course_roster(
    section_id: uuid.UUID,
    course_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Effective roster of students for ``(section_id, course_id)``,
    with each student tagged ``is_added_via_drop`` so the UI can
    distinguish original cohort members from students who joined via
    an approved add/drop batch. Students who dropped the course (or
    moved to a different section) are absent from the list.

    403 if the caller is not the assigned instructor for the pair.
    404 if the section or course does not exist.
    """
    _require_instructor(current_user)
    svc = InstructorGradingService(db)
    try:
        return await svc.get_section_course_roster(
            user_id=current_user.id,
            section_id=section_id,
            course_id=course_id,
        )
    except UnauthorizedActorError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.detail) from exc
    except EntityNotFoundError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"{exc.entity} {exc.entity_id} not found",
        ) from exc
