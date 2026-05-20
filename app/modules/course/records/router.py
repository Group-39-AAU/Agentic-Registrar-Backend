"""
Track C — Records FastAPI routes.

Two student-facing endpoints under ``/courses/records/me``:

  GET /me/grade-report?term_id=...   — official grade report (one term)
  GET /me/filing-slip?term_id=...    — registration-support slip

Both return structured JSON; PDF/signing is a later hardening PR.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.database.session import get_db
from app.modules.auth.models import User
from app.modules.course.exceptions import (
    EntityNotFoundError, StudentProfileRequiredError,
)
from app.modules.course.records.schemas import (
    FilingSlipResponse, GradeReportResponse,
)
from app.modules.course.records.service import StudentRecordsService
from app.shared.enums import UserRole


router = APIRouter(
    prefix="/courses/records",
    tags=["Course Management — Records"],
)


def _require_student(current_user: User) -> None:
    if current_user.role != UserRole.STUDENT:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only enrolled students may download their own records.",
        )


def _raise_record_errors(exc: Exception) -> None:
    if isinstance(exc, StudentProfileRequiredError):
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    if isinstance(exc, EntityNotFoundError):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"{exc.entity} {exc.entity_id} not found",
        ) from exc


@router.get(
    "/me/grade-report",
    response_model=GradeReportResponse,
    summary="Official grade report for one completed term",
)
async def get_my_grade_report(
    term_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns the official grade-report payload for ``term_id``.
    Requires the DH to have authorised the term's
    :class:`AcademicStanding` — 404 otherwise (the transcript covers
    the not-yet-authorised case).
    """
    _require_student(current_user)
    svc = StudentRecordsService(db)
    try:
        return await svc.get_grade_report(
            user_id=current_user.id, term_id=term_id,
        )
    except Exception as exc:
        _raise_record_errors(exc)
        raise


@router.get(
    "/me/filing-slip",
    response_model=FilingSlipResponse,
    summary="Filing slip — registration-support document for a term",
)
async def get_my_filing_slip(
    term_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns the filing-slip payload for ``term_id``. Requires the
    student to have a non-deleted :class:`Registration` for the
    term (works in any state — registered, payment-hold,
    add-drop-window). 404 otherwise.
    """
    _require_student(current_user)
    svc = StudentRecordsService(db)
    try:
        return await svc.get_filing_slip(
            user_id=current_user.id, term_id=term_id,
        )
    except Exception as exc:
        _raise_record_errors(exc)
        raise
