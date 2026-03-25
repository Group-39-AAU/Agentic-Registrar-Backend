"""
Testing Center module — FastAPI router.

Provides the UAT callback endpoint that simulates receiving
test scores from the physical testing center.
"""

import random
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.modules.testing_center.models import UATRecord
from app.modules.testing_center.schemas import UATCallbackResponse, UATRecordResponse
from app.shared.events import UATCompletedEvent, publish

router = APIRouter(prefix="/testing-center", tags=["Testing Center (UAT)"])


@router.post(
    "/callback/{uat_id}",
    response_model=UATCallbackResponse,
)
async def uat_callback(
    uat_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Simulate receiving UAT results from the physical testing center.

    Looks up the UAT record by its ID, generates a random score
    (weighted towards good scores 60-100), marks it complete,
    and publishes a UATCompletedEvent for the application module to handle.
    """
    # Find the UAT record
    result = await db.execute(
        select(UATRecord).where(UATRecord.uat_id == uat_id)
    )
    uat_record = result.scalar_one_or_none()

    if uat_record is None:
        raise HTTPException(404, f"No UAT record found for ID: {uat_id}")

    if uat_record.is_completed:
        raise HTTPException(400, f"UAT {uat_id} has already been completed with score {uat_record.score}")

    # Generate a weighted random score (mostly good: 60–100)
    score = round(random.triangular(60, 100, 85), 1)

    # Update the UAT record
    uat_record.score = score
    uat_record.is_completed = True

    # Publish event — undergraduate module handles its own status transition
    try:
        await publish(
            UATCompletedEvent(
                application_id=uat_record.application_id,
                applicant_id=uat_record.application_id,  # Will be resolved by handler
                uat_id=uat_id,
                score=score,
            ),
            db=db,
        )
    except Exception as e:
        raise HTTPException(400, f"Failed to process UAT completion: {str(e)}")

    await db.commit()

    return UATCallbackResponse(
        uat_id=uat_id,
        score=score,
        message=f"UAT completed successfully. Score: {score}/100",
    )




@router.get(
    "/records/{uat_id}",
    response_model=UATRecordResponse,
)
async def get_uat_record(
    uat_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Look up a UAT record by its ID."""
    result = await db.execute(
        select(UATRecord).where(UATRecord.uat_id == uat_id)
    )
    uat_record = result.scalar_one_or_none()

    if uat_record is None:
        raise HTTPException(404, f"No UAT record found for ID: {uat_id}")

    return uat_record
