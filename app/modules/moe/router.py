"""
MoE module — FastAPI router.

Read-only endpoints for inspecting the simulated MoE database.
The credential verification agent queries the DB directly.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.modules.moe.models import MoeStudentRecord
from app.modules.moe.schemas import MoeRecordResponse

router = APIRouter(prefix="/moe", tags=["MoE (Ministry of Education)"])


@router.get("/records/{admission_number}", response_model=MoeRecordResponse)
async def get_moe_record(
    admission_number: str,
    db: AsyncSession = Depends(get_db),
):
    """Look up a student's official matric record by admission number."""
    result = await db.execute(
        select(MoeStudentRecord).where(
            MoeStudentRecord.admission_number == admission_number
        )
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(404, f"No MoE record found for admission number: {admission_number}")
    return record
