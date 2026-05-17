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

from typing import Optional

from app.core.dependencies import get_current_user, get_email_service
from app.database.session import get_db
from app.modules.auth.models import User
from app.modules.course.exceptions import (
    EntityNotFoundError, InvalidAdjustmentRequestError,
    InvalidStateTransitionError, StudentProfileRequiredError,
    UnauthorizedActorError,
)
from app.modules.course.standing.schemas import (
    AcademicStandingResponse, BatchAuthoriseRequest, BatchAuthoriseResponse,
    StandingAuthoriseRequest, StandingComputeRequest, StandingComputeResponse,
    StandingDepartmentResponse, StandingOverrideRequest, StandingQueueEntry,
    StandingRosterResponse, StandingSectionResponse, StandingTermResponse,
    StudentStandingResponse, StudentStandingTranscriptResponse,
)
from app.modules.course.standing.service import StandingService
from app.shared.email.service import EmailService
from app.shared.enums import AcademicStatusType, UserRole


router = APIRouter(
    prefix="/courses/standing",
    tags=["Course Management — Academic Standing"],
)


def _raise_standing_errors(exc: Exception) -> None:
    """Shared mapper from domain exceptions to HTTP statuses."""
    if isinstance(exc, UnauthorizedActorError):
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.detail) from exc
    if isinstance(exc, StudentProfileRequiredError):
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    if isinstance(exc, EntityNotFoundError):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"{exc.entity} {exc.entity_id} not found",
        ) from exc
    if isinstance(exc, InvalidStateTransitionError):
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if isinstance(exc, InvalidAdjustmentRequestError):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, exc.detail,
        ) from exc


def _require_student(current_user: User) -> None:
    """Reject non-student callers with a uniform 403."""
    if current_user.role != UserRole.STUDENT:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only enrolled students may use this endpoint.",
        )


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


# ══════════════════════════════════════════════════════════════
#  PR C2 — Compute / Authorise / Override
# ══════════════════════════════════════════════════════════════
#
# Auth: every endpoint below is DH-only (or admin). The check lives
# inside StandingService._resolve_dh_or_403; reads can also be hit
# by a registrar officer through the queue + per-id GET below.


@router.post(
    "/terms/{term_id}/compute",
    response_model=StandingComputeResponse,
    status_code=status.HTTP_200_OK,
    summary="Run the Article-91 rules engine over a (term, scope) and upsert proposals",
)
async def compute_term_standing(
    term_id: uuid.UUID,
    payload: StandingComputeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Idempotent — re-runs upsert and bump ``computed_at``. Already-
    authorised rows are skipped (the agent never overwrites a final
    decision). Returns a summary of counts by proposed status plus
    the per-student rule trail for the DH packet.

    Scope filters in the body narrow the run:
      * ``department`` — only sections of that department.
      * ``section_id`` — only that cohort section.
      * omit both → every registered student in the term.

    403 if the caller is not a Department Head (or admin).
    """
    svc = StandingService(db)
    try:
        rows, computed, skipped = await svc.compute_term_standing(
            user_id=current_user.id,
            term_id=term_id,
            department=payload.department,
            section_id=payload.section_id,
        )
    except Exception as exc:
        _raise_standing_errors(exc)
        raise
    counts: dict[str, int] = {}
    for row in rows:
        key = row.standing.proposed_status.value
        counts[key] = counts.get(key, 0) + 1
    return StandingComputeResponse(
        term_id=term_id,
        computed_count=computed,
        skipped_count=skipped,
        counts_by_status=counts,
        rows=rows,
    )


@router.post(
    "/{standing_id}/authorise",
    response_model=AcademicStandingResponse,
    summary="DH authorises the agent's proposed status — grades become official",
)
async def authorise_standing(
    standing_id: uuid.UUID,
    payload: StandingAuthoriseRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    email_service: EmailService = Depends(get_email_service),
):
    """
    Sets ``final_status = proposed_status``, stamps the DH on the
    row, and fires a best-effort student notification. Idempotent —
    re-authorising returns the row unchanged.

    A ``reason`` is required when the standing is on hold
    (``requires_review = True``); otherwise it's optional metadata.

    403 if the caller is not a Department Head (or admin).
    409 if the standing is on hold and no reason was provided.
    """
    svc = StandingService(db, email_service=email_service)
    try:
        standing = await svc.authorise(
            user_id=current_user.id,
            standing_id=standing_id,
            reason=payload.reason,
        )
    except Exception as exc:
        _raise_standing_errors(exc)
        raise
    return AcademicStandingResponse.model_validate(standing)


@router.post(
    "/{standing_id}/override",
    response_model=AcademicStandingResponse,
    summary="DH overrides the agent's proposed status with a written reason",
)
async def override_standing(
    standing_id: uuid.UUID,
    payload: StandingOverrideRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    email_service: EmailService = Depends(get_email_service),
):
    """
    Sets ``final_status = new_status`` and records the override
    reason. The reason is mandatory (10–4000 chars enforced by the
    schema). Writes an ``OVERRIDDEN`` history entry and fires a
    best-effort student notification.

    403 if the caller is not a Department Head (or admin).
    422 if the reason is missing.
    """
    svc = StandingService(db, email_service=email_service)
    try:
        standing = await svc.override(
            user_id=current_user.id,
            standing_id=standing_id,
            new_status=payload.new_status,
            reason=payload.reason,
        )
    except Exception as exc:
        _raise_standing_errors(exc)
        raise
    return AcademicStandingResponse.model_validate(standing)


@router.get(
    "/officer/queue",
    response_model=list[StandingQueueEntry],
    summary="Officer queue of standing rows awaiting (or already past) a DH decision",
)
async def list_standing_queue(
    term_id: Optional[uuid.UUID] = None,
    department: Optional[str] = None,
    only_pending: bool = True,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Sorted oldest-first by ``computed_at``. When ``only_pending=true``
    (default), narrows to standings still waiting on a DH decision.
    Optional ``term_id`` and ``department`` filters further narrow
    the queue.
    """
    svc = StandingService(db)
    try:
        return await svc.list_queue(
            user_id=current_user.id,
            term_id=term_id,
            department=department,
            only_pending=only_pending,
        )
    except Exception as exc:
        _raise_standing_errors(exc)
        raise


@router.post(
    "/batch-authorise",
    response_model=BatchAuthoriseResponse,
    summary="DH authorises many standing rows in one call",
)
async def batch_authorise_standings(
    payload: BatchAuthoriseRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    email_service: EmailService = Depends(get_email_service),
):
    """
    Authorise up to 500 standing rows in a single request. Per-row
    outcomes are returned in ``rows[]`` with a discriminator status:

      * ``AUTHORISED``         — flipped to final_status=proposed_status
      * ``ALREADY_AUTHORISED`` — no-op (idempotent)
      * ``HELD_NEEDS_REASON``  — requires_review=True with no shared
                                 reason in the request body; skipped
      * ``NOT_FOUND``          — standing_id didn't match any row

    Optional ``reason`` is the shared written justification applied
    to any held-for-review rows in the batch. Rows that don't need
    a reason ignore it.

    403 if the caller is not a Department Head (or admin).
    """
    svc = StandingService(db, email_service=email_service)
    try:
        return await svc.batch_authorise(
            user_id=current_user.id,
            standing_ids=payload.standing_ids,
            reason=payload.reason,
        )
    except Exception as exc:
        _raise_standing_errors(exc)
        raise


@router.get(
    "/all",
    response_model=list[StandingQueueEntry],
    summary="List all AcademicStanding rows with flexible filters",
)
async def list_all_standings(
    term_id: Optional[uuid.UUID] = None,
    department: Optional[str] = None,
    student_id: Optional[uuid.UUID] = None,
    only_pending: bool = False,
    requires_review: Optional[bool] = None,
    proposed_status: Optional[AcademicStatusType] = None,
    final_status: Optional[AcademicStatusType] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Broader-purpose listing than ``/officer/queue``. Every filter is
    optional and ANDs with the others:

      * ``term_id`` / ``department`` / ``student_id`` — scope.
      * ``only_pending`` — restrict to rows still awaiting a DH
        decision (final_status NULL). Default ``false`` (return both
        pending and authorised rows).
      * ``requires_review`` — narrow to (or exclude) held rows.
      * ``proposed_status`` / ``final_status`` — exact enum match.

    Sorted oldest-first by ``computed_at``. Auth: officer / DH /
    admin (mirrors the other reads in this module).
    """
    svc = StandingService(db)
    try:
        return await svc.list_standings(
            user_id=current_user.id,
            term_id=term_id,
            department=department,
            student_id=student_id,
            only_pending=only_pending,
            requires_review=requires_review,
            proposed_status=proposed_status,
            final_status=final_status,
        )
    except Exception as exc:
        _raise_standing_errors(exc)
        raise


# ══════════════════════════════════════════════════════════════
#  PR C3 — Student-facing endpoints
# ══════════════════════════════════════════════════════════════
#
# Both endpoints below filter to AUTHORISED standings only — a
# student never sees the agent's pending proposal, only what the DH
# has officially decided. Same pattern as Track B's transcript.


@router.get(
    "/me",
    response_model=StudentStandingTranscriptResponse,
    summary="Calling student's authorised academic standings across every term",
)
async def get_my_standings(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Every AUTHORISED ``AcademicStanding`` the calling student has,
    newest-first by term start_date. Each entry carries the SGPA,
    CGPA, status, and a plain-language explanation (the DH's
    override reason when one was written, otherwise the rules-engine
    sentence captured at compute time). ``current_status`` is the
    latest authorised status, useful for dashboard rendering.

    Pending (final_status NULL) and held-for-review standings are
    invisible to students — they only see official decisions.

    403 if the caller is not a STUDENT or has no Student profile.
    """
    _require_student(current_user)
    svc = StandingService(db)
    try:
        return await svc.get_my_standings(user_id=current_user.id)
    except Exception as exc:
        _raise_standing_errors(exc)
        raise


@router.get(
    "/me/terms/{term_id}",
    response_model=StudentStandingResponse,
    summary="Calling student's authorised academic standing for one term",
)
async def get_my_term_standing(
    term_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Single-term version of ``/me``. Useful for the per-term tab in
    the student portal.

    404 if no authorised standing exists for the (student, term) —
    e.g. the term hasn't been computed yet, or the DH hasn't
    authorised the proposal yet.
    """
    _require_student(current_user)
    svc = StandingService(db)
    try:
        return await svc.get_my_term_standing(
            user_id=current_user.id, term_id=term_id,
        )
    except Exception as exc:
        _raise_standing_errors(exc)
        raise


@router.get(
    "/{standing_id}",
    response_model=AcademicStandingResponse,
    summary="Read one standing row in full",
)
async def get_standing(
    standing_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Officer-or-DH read of a single AcademicStanding row."""
    svc = StandingService(db)
    try:
        standing = await svc.get_standing(
            user_id=current_user.id, standing_id=standing_id,
        )
    except Exception as exc:
        _raise_standing_errors(exc)
        raise
    return AcademicStandingResponse.model_validate(standing)
