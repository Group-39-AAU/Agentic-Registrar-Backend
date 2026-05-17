"""
Track C (Academic Standing) — FastAPI routes.

PR C1 ships four reads powering the DH browse flow:

    GET  /courses/standing/terms
    GET  /courses/standing/terms/{tid}/departments
    GET  /courses/standing/terms/{tid}/departments/{dept}/sections
    GET  /courses/standing/terms/{tid}/sections/{sid}/students

Auth: REGISTRAR_OFFICER, DEPARTMENT_HEAD (via CourseManagementOfficer
row), or ADMIN. The role check happens inside the service so the
same gate applies to any future caller.

PR C2 will add the POST /compute / /authorise / /override
endpoints to this router.
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
from app.modules.course.standing.schemas import (
    StandingDepartmentResponse, StandingRosterResponse,
    StandingSectionResponse, StandingTermResponse,
)
from app.modules.course.standing.service import StandingService


router = APIRouter(
    prefix="/courses/standing",
    tags=["Course Management — Academic Standing"],
)


def _raise_standing_errors(exc: Exception) -> None:
    """Shared mapper from domain exceptions to HTTP statuses."""
    if isinstance(exc, UnauthorizedActorError):
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.detail) from exc
    if isinstance(exc, EntityNotFoundError):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"{exc.entity} {exc.entity_id} not found",
        ) from exc


# ── Dropdown 1: terms ───────────────────────────────────────────


@router.get(
    "/terms",
    response_model=list[StandingTermResponse],
    summary="Terms available for academic-standing review",
)
async def list_terms(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Every term, newest-first. Each entry carries
    ``has_authorised_grades`` so the UI can grey out terms with no
    grades to evaluate yet. Officers typically pick a closed term
    whose grades have been authorised through Track B's DH workflow.
    """
    svc = StandingService(db)
    try:
        return await svc.list_terms(user_id=current_user.id)
    except Exception as exc:
        _raise_standing_errors(exc)
        raise


# ── Dropdown 2: departments in a term ───────────────────────────


@router.get(
    "/terms/{term_id}/departments",
    response_model=list[StandingDepartmentResponse],
    summary="Departments with sections in the chosen term",
)
async def list_departments(
    term_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Every department that owns at least one Section in the term,
    plus section + student counts to hint at workload before
    drilling in.
    """
    svc = StandingService(db)
    try:
        return await svc.list_departments(
            user_id=current_user.id, term_id=term_id,
        )
    except Exception as exc:
        _raise_standing_errors(exc)
        raise


# ── Dropdown 3: sections in a (term, department) ────────────────


@router.get(
    "/terms/{term_id}/departments/{department}/sections",
    response_model=list[StandingSectionResponse],
    summary="Sections of a department in the chosen term",
)
async def list_sections(
    term_id: uuid.UUID,
    department: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Sections sorted by (semester, section_code). Empty list when
    the department has no sections in the term.
    """
    svc = StandingService(db)
    try:
        return await svc.list_sections(
            user_id=current_user.id,
            term_id=term_id, department=department,
        )
    except Exception as exc:
        _raise_standing_errors(exc)
        raise


# ── Roster: students with live SGPA / CGPA preview ──────────────


@router.get(
    "/terms/{term_id}/sections/{section_id}/students",
    response_model=StandingRosterResponse,
    summary="Cohort roster with live-computed SGPA / CGPA and grade preview",
)
async def get_section_roster(
    term_id: uuid.UUID,
    section_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Every registered cohort member of (term, section), each with:

      * authorised grades for the target term (with add/drop context),
      * live-computed SGPA for the term,
      * live-computed CGPA across all terms,
      * F-count + Article-91 evaluation context flags,
      * the existing ``AcademicStanding`` row when one has been
        computed (PR C2 territory — null on this PR).

    Students who dropped the cohort or who appear via add/drop into
    another section are filtered to the appropriate roster by the
    standard Track A semantics — this view is anchored on
    ``Registration.section_id``.
    """
    svc = StandingService(db)
    try:
        return await svc.get_section_roster(
            user_id=current_user.id,
            term_id=term_id, section_id=section_id,
        )
    except Exception as exc:
        _raise_standing_errors(exc)
        raise
