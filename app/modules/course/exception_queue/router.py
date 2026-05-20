"""
Unified exception queue — FastAPI route.

One read endpoint at ``GET /courses/exceptions/queue``. The
``sources`` query parameter narrows which per-source queries fire;
omit to get everything. ``term_id`` and ``student_id`` further
scope the join.

Auth: REGISTRAR_OFFICER (with backing CourseManagementOfficer row)
∨ DEPARTMENT_HEAD ∨ ADMIN.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.database.session import get_db
from app.modules.auth.models import User
from app.modules.course.exception_queue.schemas import (
    ExceptionQueueEntry, ExceptionSource,
)
from app.modules.course.exception_queue.service import ExceptionQueueService
from app.modules.course.exceptions import UnauthorizedActorError


router = APIRouter(
    prefix="/courses/exceptions",
    tags=["Course Management — Exception Queue"],
)


@router.get(
    "/queue",
    response_model=list[ExceptionQueueEntry],
    summary="Unified queue of pending exceptions across Tracks A/B/C",
)
async def list_exception_queue(
    sources: Optional[list[ExceptionSource]] = Query(
        default=None,
        description=(
            "Narrow to one or more source queues. Omit to query all "
            "three (ADVISORY, GRADING, STANDING)."
        ),
    ),
    term_id: Optional[uuid.UUID] = None,
    student_id: Optional[uuid.UUID] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Joined view of all pending exceptions awaiting an officer
    decision. Each row carries a ``source`` discriminator and a
    ``deep_link`` to the source-specific resolution endpoint —
    advisory close, DH grade-batch authorise/reject, or standing
    authorise/override.

    Sorted newest-first by the row's source-timestamp
    (advisory.created_at / batch.submitted_at / standing.computed_at).

    Returns an empty list (200) when there's nothing pending. 403
    if the caller is not an officer / DH / admin.
    """
    svc = ExceptionQueueService(db)
    try:
        return await svc.list_pending(
            user_id=current_user.id,
            sources=set(sources) if sources else None,
            term_id=term_id,
            student_id=student_id,
        )
    except UnauthorizedActorError as exc:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, exc.detail,
        ) from exc
