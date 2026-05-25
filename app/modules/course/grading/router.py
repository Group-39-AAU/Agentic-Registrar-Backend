"""
Track B (Grading) — FastAPI routes.

Mounted from ``app.main`` under the ``/courses/grading`` prefix.

PR 1 ships two reads (instructor surface):

    GET  /courses/grading/me/sections?term_id={uuid}
    GET  /courses/grading/sections/{section_id}/courses/{course_id}/roster

PR 2 adds the breakdown editor + grade-entry batch lifecycle (still
instructor-only):

    POST /courses/grading/sections/{sid}/courses/{cid}/breakdown
    GET  /courses/grading/sections/{sid}/courses/{cid}/breakdown
    POST /courses/grading/sections/{sid}/courses/{cid}/batch
    PUT  /courses/grading/batches/{bid}/scores
    POST /courses/grading/batches/{bid}/submit

Ownership of the (section, course) pair is enforced inside the
service layer, not in the route handler, so the same rules apply if
other paths reuse the service later.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from typing import Optional

from app.core.dependencies import get_current_user
from app.database.session import get_db
from app.modules.auth.models import User
from app.modules.course.exceptions import (
    BreakdownLockedError, DepartmentHeadRoleRequiredError,
    EntityNotFoundError, GradeBatchNotEditableError,
    GradeBatchNotReviewableError, IncompleteGradeSubmissionError,
    InvalidBreakdownError, JustificationRequiredError,
    StudentProfileRequiredError, UnauthorizedActorError,
)
from app.modules.course.grading.dh_service import DepartmentHeadGradingService
from app.modules.course.grading.schemas import (
    AgentRerunResponse, AssessmentBreakdownCreate,
    AssessmentBreakdownResponse, BulkScoreWrite,
    DepartmentHeadDecisionRequest, DepartmentHeadDecisionResponse,
    DepartmentHeadQueueEntry, GradeAgentReviewResponse,
    GradeBatchResponse, GradeBatchReviewPacketResponse,
    GradeBatchSubmitResponse, InstructorJustificationRequest,
    InstructorSectionAssignmentResponse, QueueDepartmentOption,
    SectionCourseRosterResponse, TranscriptResponse, TranscriptTermEntry,
)
from app.modules.course.grading.service import InstructorGradingService
from app.modules.course.grading.transcript_service import (
    StudentTranscriptService,
)
from app.shared.enums import GradeSubmissionStatus, UserRole


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


# ══════════════════════════════════════════════════════════════
#  PR 2 — Breakdown editor + grade-entry batch
# ══════════════════════════════════════════════════════════════


def _raise_grading_errors(exc: Exception) -> None:
    """
    Shared mapper from grading-specific domain exceptions to HTTP
    statuses. Re-raises anything it doesn't recognise.
    """
    if isinstance(exc, UnauthorizedActorError):
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.detail) from exc
    if isinstance(exc, DepartmentHeadRoleRequiredError):
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    if isinstance(exc, StudentProfileRequiredError):
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    if isinstance(exc, EntityNotFoundError):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"{exc.entity} {exc.entity_id} not found",
        ) from exc
    if isinstance(exc, BreakdownLockedError):
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if isinstance(exc, GradeBatchNotEditableError):
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if isinstance(exc, GradeBatchNotReviewableError):
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if isinstance(exc, JustificationRequiredError):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc),
        ) from exc
    if isinstance(exc, InvalidBreakdownError):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, exc.detail,
        ) from exc
    if isinstance(exc, IncompleteGradeSubmissionError):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {
                "code": "incomplete_grade_submission",
                "message": str(exc),
                "missing": exc.missing,
            },
        ) from exc


# ── Breakdown CRUD ──


@router.post(
    "/sections/{section_id}/courses/{course_id}/breakdown",
    response_model=AssessmentBreakdownResponse,
    status_code=status.HTTP_200_OK,
    summary="Create or replace the assessment breakdown for a (section, course)",
)
async def upsert_breakdown(
    section_id: uuid.UUID,
    course_id: uuid.UUID,
    payload: AssessmentBreakdownCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    The breakdown is the instructor's plan for how the final grade
    is composed. Components' weights must sum to exactly 100.

    A second POST that replaces the breakdown bumps ``version`` and
    is allowed only while ``locked_at`` is null (no component scores
    saved yet). Once any score is entered the breakdown locks and a
    further POST returns 409.

    422 if the payload is invalid (weights don't sum to 100, duplicate
    component names, weight out of (0,100]).
    """
    _require_instructor(current_user)
    svc = InstructorGradingService(db)
    try:
        return await svc.upsert_breakdown(
            user_id=current_user.id,
            section_id=section_id, course_id=course_id,
            payload=payload,
        )
    except Exception as exc:
        _raise_grading_errors(exc)
        raise


@router.get(
    "/sections/{section_id}/courses/{course_id}/breakdown",
    response_model=AssessmentBreakdownResponse,
    summary="Read the assessment breakdown for a (section, course)",
)
async def get_breakdown(
    section_id: uuid.UUID,
    course_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """404 if no breakdown has been POSTed for this pair yet."""
    _require_instructor(current_user)
    svc = InstructorGradingService(db)
    try:
        return await svc.get_breakdown(
            user_id=current_user.id,
            section_id=section_id, course_id=course_id,
        )
    except Exception as exc:
        _raise_grading_errors(exc)
        raise


# ── Batch get-or-create + reads ──


@router.post(
    "/sections/{section_id}/courses/{course_id}/batch",
    response_model=GradeBatchResponse,
    status_code=status.HTTP_200_OK,
    summary="Get (or lazily create) the grade batch for a (section, course)",
)
async def get_or_create_batch(
    section_id: uuid.UUID,
    course_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Idempotent: returns the existing batch, or creates an empty DRAFT
    one if none exists yet. The response carries the current score
    matrix (one cell per (roster student × component)) so the UI can
    render the grade-entry grid in a single request.

    404 if no breakdown has been created for the pair yet.
    """
    _require_instructor(current_user)
    svc = InstructorGradingService(db)
    try:
        return await svc.get_or_create_batch(
            user_id=current_user.id,
            section_id=section_id, course_id=course_id,
        )
    except Exception as exc:
        _raise_grading_errors(exc)
        raise


# ── Score upsert (draft save) ──


@router.put(
    "/batches/{batch_id}/scores",
    response_model=GradeBatchResponse,
    summary="Bulk-upsert one or more (student, component) score cells",
)
async def upsert_scores(
    batch_id: uuid.UUID,
    payload: BulkScoreWrite,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Save partial work without committing the batch. Validates that
    every cell's student is on the live roster and that the score is
    within ``[0, component.max_score]``. The first non-null save
    locks the breakdown.

    409 if the batch is no longer in DRAFT.
    422 if any cell is malformed (unknown student, unknown component,
    score over max_score).
    """
    _require_instructor(current_user)
    svc = InstructorGradingService(db)
    try:
        return await svc.upsert_scores(
            user_id=current_user.id,
            batch_id=batch_id, payload=payload,
        )
    except Exception as exc:
        _raise_grading_errors(exc)
        raise


# ── Clear scores + unlock breakdown ──


@router.delete(
    "/batches/{batch_id}/scores",
    response_model=GradeBatchResponse,
    summary="Wipe all entered scores and unlock the breakdown for re-upload",
)
async def delete_all_scores(
    batch_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Destructive: clears every (student × component) cell for the
    batch and resets the breakdown's lock. Use this when the
    instructor needs to change the breakdown after already entering
    some scores.

    The batch row itself is preserved (same id, status stays DRAFT)
    so any saved frontend reference keeps working.

    409 if the batch is not in DRAFT (post-submit edits go through
    the iteration / DH-review path, not this one).
    """
    _require_instructor(current_user)
    svc = InstructorGradingService(db)
    try:
        return await svc.delete_all_scores(
            user_id=current_user.id, batch_id=batch_id,
        )
    except Exception as exc:
        _raise_grading_errors(exc)
        raise


# ── Submit ──


@router.post(
    "/batches/{batch_id}/submit",
    response_model=GradeBatchSubmitResponse,
    summary="Submit the batch — computes letters and runs the agent",
)
async def submit_batch(
    batch_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    DRAFT → SUBMITTED (or FLAGGED if the LLM-driven
    GradingMonitorAgent flags it). The endpoint:

      1. Refuses if any roster student is missing any component score
         (422 with a structured ``missing`` list for the UI to highlight).
      2. Computes weighted_pct + AAU letter per student.
      3. Upserts ``Grade`` rows at status=SUBMITTED.
      4. Asks the GradingMonitorAgent (LLM as department head, with
         deterministic tool evidence) for a verdict.
      5. Persists the verdict + tool findings + LLM reasoning into
         ``grade_agent_reviews``.
      6. Returns the verdict and per-student outcomes.

    Possible ``agent_verdict`` values:
      - APPROVE  → batch transitions to SUBMITTED (awaits DH auth).
      - FLAG     → batch transitions to FLAGGED. Instructor calls
                   POST ``/justify`` (with a written reason) or POST
                   ``/reopen`` (to edit scores) to iterate.
      - PENDING  → LLM was unavailable. Batch stays SUBMITTED; the
                   DH workflow (PR 4) will re-trigger the agent.

    409 if the batch isn't in DRAFT.
    """
    _require_instructor(current_user)
    svc = InstructorGradingService(db)
    try:
        return await svc.submit_batch(
            user_id=current_user.id, batch_id=batch_id,
        )
    except Exception as exc:
        _raise_grading_errors(exc)
        raise


# ══════════════════════════════════════════════════════════════
#  PR 3 — Iteration loop after a FLAG
# ══════════════════════════════════════════════════════════════


@router.post(
    "/batches/{batch_id}/justify",
    response_model=GradeBatchSubmitResponse,
    summary="Attach instructor justification to a FLAGGED batch and re-run agent",
)
async def submit_justification(
    batch_id: uuid.UUID,
    payload: InstructorJustificationRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    The instructor writes a justification (10-4000 chars) explaining
    why the agent's prior FLAG concerns are unfounded. The agent is
    re-run with the justification appended to its context; the LLM
    may APPROVE on this iteration or maintain the FLAG with a
    refined explanation.

    ``iteration_count`` bumps; a new ``grade_agent_reviews`` row is
    appended. From any status other than FLAGGED this is a 409.
    """
    _require_instructor(current_user)
    svc = InstructorGradingService(db)
    try:
        return await svc.submit_justification(
            user_id=current_user.id,
            batch_id=batch_id,
            justification=payload.justification,
        )
    except Exception as exc:
        _raise_grading_errors(exc)
        raise


@router.post(
    "/batches/{batch_id}/reopen",
    response_model=GradeBatchResponse,
    summary="Move a FLAGGED or REJECTED batch back to DRAFT for editing",
)
async def reopen_batch(
    batch_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Two entry points use this:

      - Instructor accepts the agent's FLAG and wants to fix scores
        rather than justify them (FLAGGED → DRAFT).
      - The department head REJECTED the batch and the instructor
        needs to redo it (REJECTED → DRAFT).

    ``iteration_count`` bumps so the next submit's agent run records
    on the right iteration. The breakdown stays locked (cell values
    survive); ``DELETE /scores`` is the path for a full reset.

    409 if the batch isn't FLAGGED or REJECTED.
    """
    _require_instructor(current_user)
    svc = InstructorGradingService(db)
    try:
        return await svc.reopen_batch(
            user_id=current_user.id, batch_id=batch_id,
        )
    except Exception as exc:
        _raise_grading_errors(exc)
        raise


# ══════════════════════════════════════════════════════════════
#  PR 3 — Agent review history (instructor read; DH in PR 4)
# ══════════════════════════════════════════════════════════════


@router.get(
    "/batches/{batch_id}/agent-reviews",
    response_model=list[GradeAgentReviewResponse],
    summary="Append-only history of GradingMonitorAgent runs for this batch",
)
async def list_agent_reviews(
    batch_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns every agent run on this batch, most-recent-first. Each
    row carries the verdict (APPROVE / FLAG / PENDING), the LLM's
    plain-English reasoning, the structured flags, the full tool
    context the LLM saw, and which agent instance ran. Useful to
    the instructor for understanding the last verdict, and to the
    department head (PR 4) for seeing the full iteration history.
    """
    _require_instructor(current_user)
    svc = InstructorGradingService(db)
    try:
        return await svc.list_agent_reviews(
            user_id=current_user.id, batch_id=batch_id,
        )
    except Exception as exc:
        _raise_grading_errors(exc)
        raise


# ══════════════════════════════════════════════════════════════
#  PR 4 — Department-head workflow
# ══════════════════════════════════════════════════════════════
#
# Auth: every route below requires the calling user to be either
# an officer with role=DEPARTMENT_HEAD or a UserRole.ADMIN. The
# check lives in the service layer (mirroring how Track A gates
# DH-only endpoints) so the rule travels with the code that
# enforces it.


@router.get(
    "/officer/queue/departments",
    response_model=list[QueueDepartmentOption],
    summary="Departments with pending batches — populates the queue filter dropdown",
)
async def list_queue_departments(
    term_id: Optional[uuid.UUID] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns the distinct departments that currently have at least
    one batch awaiting a DH decision, each with a pending count.
    The frontend uses this to build the queue's department-filter
    dropdown so a DH never types a free-text department string
    (no typos, no silent empty results). Optionally scoped to a
    term to match the term the DH is viewing.

    403 if the caller is not a department head (or admin).
    """
    svc = DepartmentHeadGradingService(db)
    try:
        return await svc.list_queue_departments(
            user_id=current_user.id, term_id=term_id,
        )
    except Exception as exc:
        _raise_grading_errors(exc)
        raise


@router.get(
    "/officer/queue",
    response_model=list[DepartmentHeadQueueEntry],
    summary="Batches in the requested statuses for the caller's department",
)
async def list_dh_queue(
    term_id: Optional[uuid.UUID] = None,
    department: Optional[str] = None,
    status_filter: Optional[str] = Query(default=None, alias="status"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Pending batches by default (``SUBMITTED`` + ``FLAGGED``), sorted
    oldest-first. Pass ``status`` as a comma-separated list (e.g.
    ``AUTHORISED`` or ``AUTHORISED,REJECTED``) to fetch terminal
    history — terminal lists sort most-recent-first.

    Department auto-scopes to the calling Department Head's own
    department; admins see every department unless they pass
    ``department``.

    403 if the caller is not a department head (or admin).
    422 if ``status`` contains an unknown grade-submission status.
    """
    statuses: Optional[set[GradeSubmissionStatus]] = None
    if status_filter:
        try:
            statuses = {
                GradeSubmissionStatus(piece.strip().upper())
                for piece in status_filter.split(",") if piece.strip()
            } or None
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Unknown status value: {exc}",
            ) from exc
    svc = DepartmentHeadGradingService(db)
    try:
        return await svc.list_queue(
            user_id=current_user.id,
            term_id=term_id, department=department,
            statuses=statuses,
        )
    except Exception as exc:
        _raise_grading_errors(exc)
        raise


@router.get(
    "/officer/batches/{batch_id}",
    response_model=GradeBatchReviewPacketResponse,
    summary="Full department-head review packet for one batch",
)
async def get_dh_review_packet(
    batch_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns the full payload the DH needs to make a decision:

      - The live batch (breakdown + per-student score matrix).
      - Per-student computed letters (the official outcome each
        roster member would receive if the DH authorises).
      - The append-only history of every agent run (with verdict,
        flags, reasoning, full tool findings).
      - Every prior department-head decision on this batch.
    """
    svc = DepartmentHeadGradingService(db)
    try:
        return await svc.get_review_packet(
            user_id=current_user.id, batch_id=batch_id,
        )
    except Exception as exc:
        _raise_grading_errors(exc)
        raise


@router.post(
    "/officer/batches/{batch_id}/authorise",
    response_model=DepartmentHeadDecisionResponse,
    summary="Authorise a batch — grades become official",
)
async def authorise_batch(
    batch_id: uuid.UUID,
    payload: DepartmentHeadDecisionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Final acceptance:

      - SUBMITTED → AUTHORISED  (decision = AUTHORISED;
        ``justification`` optional — accepting a clean
        agent-APPROVE needs no written reason)
      - FLAGGED   → AUTHORISED  (decision = OVERRODE_AGENT_FLAG;
        ``justification`` REQUIRED — the DH is overriding the
        agent's concerns and the audit trail needs to record why)

    Side effects: every per-student ``Grade`` row is stamped
    AUTHORISED, ``authorised_by_id`` / ``authorised_at`` populated,
    and the immutable audit row is appended.

    409 if the batch isn't in SUBMITTED or FLAGGED.
    422 if overriding a FLAG without a justification.
    """
    svc = DepartmentHeadGradingService(db)
    try:
        return await svc.authorise(
            user_id=current_user.id,
            batch_id=batch_id,
            justification=payload.justification,
        )
    except Exception as exc:
        _raise_grading_errors(exc)
        raise


@router.post(
    "/officer/batches/{batch_id}/reject",
    response_model=DepartmentHeadDecisionResponse,
    summary="Reject a batch — sends it back to the instructor with a reason",
)
async def reject_batch(
    batch_id: uuid.UUID,
    payload: DepartmentHeadDecisionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Final rejection — the instructor must redo the work:

      - FLAGGED   → REJECTED  (decision = REJECTED — DH agrees
        with the agent's flag)
      - SUBMITTED → REJECTED  (decision = OVERRODE_AGENT_APPROVAL —
        DH disagrees with the agent's approval)

    ``justification`` is REQUIRED on both paths — the instructor
    reads it and uses it to fix the batch. The instructor's next
    step is ``POST /batches/{bid}/reopen`` (REJECTED → DRAFT) to
    edit scores.

    409 if the batch isn't in SUBMITTED or FLAGGED.
    422 if the justification is missing.
    """
    svc = DepartmentHeadGradingService(db)
    try:
        return await svc.reject(
            user_id=current_user.id,
            batch_id=batch_id,
            justification=payload.justification or "",
        )
    except Exception as exc:
        _raise_grading_errors(exc)
        raise


@router.post(
    "/officer/batches/{batch_id}/rerun-agent",
    response_model=AgentRerunResponse,
    summary="Re-invoke the GradingMonitorAgent (typically after a PENDING verdict)",
)
async def rerun_agent(
    batch_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    The DH triggers a fresh agent run. Use when the prior agent
    verdict was PENDING (LLM was unavailable) and the DH wants the
    agent's actual judgement before deciding. Writes a new
    ``grade_agent_reviews`` row; transitions the batch's status to
    SUBMITTED (on APPROVE) or FLAGGED (on FLAG) — does NOT write a
    DH-decision row.

    409 if the batch isn't in SUBMITTED or FLAGGED.
    """
    svc = DepartmentHeadGradingService(db)
    try:
        return await svc.rerun_agent(
            user_id=current_user.id, batch_id=batch_id,
        )
    except Exception as exc:
        _raise_grading_errors(exc)
        raise


# ══════════════════════════════════════════════════════════════
#  PR 4 — Student transcript
# ══════════════════════════════════════════════════════════════


def _require_student(current_user: User) -> None:
    if current_user.role != UserRole.STUDENT:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only enrolled students may view a transcript through this endpoint.",
        )


@router.get(
    "/me/transcript",
    response_model=TranscriptResponse,
    summary="Calling student's transcript across every term",
)
async def get_my_transcript(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Every AUTHORISED grade the student has received, grouped by
    term (newest first). For each course where a Track-B
    ``GradeBatch`` exists, the per-component breakdown comes
    through with the student's raw scores and weighted contribution.

    SUBMITTED / FLAGGED / REJECTED / DRAFT grades are invisible —
    only AUTHORISED grades reach the student.
    """
    _require_student(current_user)
    svc = StudentTranscriptService(db)
    try:
        return await svc.get_transcript(user_id=current_user.id)
    except Exception as exc:
        _raise_grading_errors(exc)
        raise


@router.get(
    "/me/terms/{term_id}/grades",
    response_model=TranscriptTermEntry,
    summary="Calling student's grades for a specific term",
)
async def get_my_term_grades(
    term_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    AUTHORISED grades only, for the given term. Useful for a
    one-term view in the student portal without loading the full
    transcript.

    404 if the term doesn't exist.
    """
    _require_student(current_user)
    svc = StudentTranscriptService(db)
    try:
        return await svc.get_term_grades(
            user_id=current_user.id, term_id=term_id,
        )
    except Exception as exc:
        _raise_grading_errors(exc)
        raise
