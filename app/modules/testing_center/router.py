"""
Testing Center module — FastAPI router.

Provides the UAT callback endpoint that simulates receiving
test scores from the physical testing center.
"""

import html
import random
from typing import Tuple

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.database.session import get_db
from app.modules.testing_center.models import UATRecord
from app.modules.testing_center.schemas import UATCallbackResponse, UATRecordResponse
from app.modules.undergraduate.models import UndergraduateApplication
from app.shared.events import UATCompletedEvent, publish

router = APIRouter(prefix="/testing-center", tags=["Testing Center (UAT)"])


async def _complete_uat(
    uat_id: str,
    db: AsyncSession,
) -> Tuple[UATRecord, float]:
    """
    Look up UAT, assign score, publish UATCompletedEvent.
    Caller must commit. Returns (record, score).
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
    app_result = await db.execute(
        select(UndergraduateApplication.applicant_id).where(
            UndergraduateApplication.id == uat_record.application_id
        )
    )
    applicant_id = app_result.scalar_one()

    try:
        await publish(
            UATCompletedEvent(
                application_id=uat_record.application_id,
                applicant_id=applicant_id,
                uat_id=uat_id,
                score=score,
            ),
            db=db,
        )
    except Exception as e:
        raise HTTPException(400, f"Failed to process UAT completion: {str(e)}") from e

    return uat_record, score


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
    _, score = await _complete_uat(uat_id, db)
    await db.commit()

    return UATCallbackResponse(
        uat_id=uat_id,
        score=score,
        message=f"UAT completed successfully. Score: {score}/100",
    )


@router.get(
    "/uat-session/{uat_id}",
    response_class=HTMLResponse,
    summary="UAT simulation landing page (from email link)",
)
async def uat_simulation_session(uat_id: str):
    """
    Browser-friendly page with a form POST to the callback.

    Email links point here (GET has no side effects). The applicant
    submits the form to POST /callback/{uat_id}, which records the score.
    Avoids mail clients that prefetch GET links from completing the UAT.
    """
    safe_id = html.escape(uat_id, quote=True)
    base = settings.PUBLIC_APP_BASE_URL.rstrip("/")
    prefix = settings.API_V1_PREFIX
    if not prefix.startswith("/"):
        prefix = "/" + prefix
    action = f"{base}{prefix}/testing-center/callback/{uat_id}"

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>UAT simulation — {safe_id}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 32rem; margin: 2rem auto; padding: 0 1rem; color: #0f172a; }}
    h1 {{ font-size: 1.25rem; }}
    p {{ color: #475569; line-height: 1.5; }}
    button {{ background: #2563eb; color: #fff; border: none; padding: 0.75rem 1.5rem; border-radius: 999px; font-weight: 600; cursor: pointer; font-size: 1rem; }}
    button:hover {{ background: #1d4ed8; }}
    .ref {{ font-family: ui-monospace, monospace; color: #1d4ed8; }}
  </style>
</head>
<body>
  <h1>Record simulated UAT result</h1>
  <p>UAT reference: <span class="ref">{safe_id}</span></p>
  <p>This simulates completing the on-site UAT and sending the result to the registrar system. Click only when you are ready to record your simulated score.</p>
  <form method="post" action="{html.escape(action, quote=True)}">
    <button type="submit">Take test (simulate)</button>
  </form>
</body>
</html>"""
    return HTMLResponse(content=page)


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
