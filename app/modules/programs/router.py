"""
Programs module — FastAPI router.

Read-only endpoints for students to browse available programs.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.modules.programs.models import AcademicProgram
from app.modules.programs.schemas import ProgramListResponse, ProgramResponse
from app.shared.enums import StreamType

router = APIRouter(prefix="/programs", tags=["Programs"])


@router.get("", response_model=ProgramListResponse)
async def list_programs(
    stream: Optional[StreamType] = Query(None, description="Filter by stream"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """
    List all active academic programs.
    Optionally filter by stream (NATURAL / SOCIAL) so self-sponsored
    students can pick their top 3 from the correct stream.
    """
    base = select(AcademicProgram).where(
        AcademicProgram.is_active == True,  # noqa: E712
        AcademicProgram.is_deleted == False,  # noqa: E712
    )
    count_q = select(func.count()).select_from(AcademicProgram).where(
        AcademicProgram.is_active == True,  # noqa: E712
        AcademicProgram.is_deleted == False,  # noqa: E712
    )

    if stream is not None:
        base = base.where(AcademicProgram.stream == stream)
        count_q = count_q.where(AcademicProgram.stream == stream)

    total = (await db.execute(count_q)).scalar_one()
    result = await db.execute(
        base.order_by(AcademicProgram.name).limit(limit).offset(offset)
    )
    items = result.scalars().all()

    return ProgramListResponse(items=items, total=total)


@router.get("/{program_id}", response_model=ProgramResponse)
async def get_program(
    program_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get a specific program by ID."""
    import uuid as _uuid
    try:
        pid = _uuid.UUID(program_id)
    except ValueError:
        from fastapi import HTTPException
        raise HTTPException(400, "Invalid program ID")

    result = await db.execute(
        select(AcademicProgram).where(
            AcademicProgram.id == pid,
            AcademicProgram.is_deleted == False,  # noqa: E712
        )
    )
    program = result.scalar_one_or_none()
    if program is None:
        from fastapi import HTTPException
        raise HTTPException(404, "Program not found")
    return program
