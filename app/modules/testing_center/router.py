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
from app.modules.undergraduate.service import ApplicationService
from app.shared.enums import ApplicationStatus, UserRole

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
    and transitions the application to UAT_COMPLETED.
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
    # Use a triangular distribution weighted toward higher scores
    score = round(random.triangular(60, 100, 85), 1)

    # Update the UAT record
    uat_record.score = score
    uat_record.is_completed = True

    # Transition application: UAT_PENDING → UAT_COMPLETED
    svc = ApplicationService(db)
    from app.modules.undergraduate.schemas import ApplicationStatusUpdate as StatusUpd

    try:
        app_entity = await svc.get_application(uat_record.application_id)
        await svc.change_status(
            uat_record.application_id,
            StatusUpd(
                new_status=ApplicationStatus.UAT_COMPLETED,
                trigger_reason=f"UAT completed — score: {score}/100",
            ),
            actor_id=app_entity.applicant_id,  # Use applicant's ID as actor to satisfy FK constraints
            actor_role=UserRole.SYSTEM,
        )
    except Exception as e:
        raise HTTPException(400, f"Failed to update application status: {str(e)}")

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
