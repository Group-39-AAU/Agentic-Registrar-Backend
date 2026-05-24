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
    callback_url = html.escape(
        f"{base}{prefix}/testing-center/callback/{uat_id}", quote=True
    )

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>UAT simulation — {safe_id}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ font-family: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif; max-width: 32rem; margin: 2rem auto; padding: 0 1rem; color: #0f172a; background: #f4f6f8; }}
    .card {{ background: #fff; border-radius: 12px; padding: 1.75rem 1.5rem; box-shadow: 0 4px 24px rgba(15,23,42,0.08); }}
    h1 {{ font-size: 1.25rem; margin: 0 0 .75rem; }}
    p {{ color: #475569; line-height: 1.5; }}
    button {{ background: #2563eb; color: #fff; border: none; padding: 0.75rem 1.5rem; border-radius: 999px; font-weight: 600; cursor: pointer; font-size: 1rem; }}
    button:hover {{ background: #1d4ed8; }}
    button:disabled {{ background: #94a3b8; cursor: not-allowed; }}
    .ref {{ font-family: ui-monospace, monospace; color: #1d4ed8; }}

    /* Popup overlay + modal */
    .overlay {{
      position: fixed; inset: 0; background: rgba(15,23,42,0.55);
      display: none; align-items: center; justify-content: center;
      padding: 1rem; z-index: 50;
    }}
    .overlay.is-open {{ display: flex; }}
    .modal {{
      background: #fff; border-radius: 14px; max-width: 26rem; width: 100%;
      padding: 1.75rem 1.5rem; text-align: center;
      box-shadow: 0 12px 48px rgba(15,23,42,0.25);
      animation: pop .18s ease-out;
    }}
    @keyframes pop {{ from {{ transform: scale(.94); opacity: 0; }} to {{ transform: scale(1); opacity: 1; }} }}
    .icon {{
      width: 56px; height: 56px; border-radius: 50%; margin: 0 auto .75rem;
      display: flex; align-items: center; justify-content: center;
      font-size: 28px; font-weight: 700; color: #fff;
    }}
    .icon-ok {{ background: #16a34a; }}
    .icon-err {{ background: #dc2626; }}
    .modal h2 {{ margin: 0 0 .5rem; font-size: 1.15rem; color: #0f172a; }}
    .modal .score {{
      display: inline-block; margin: .5rem 0 1rem;
      padding: .5rem 1.25rem; background: #ecfdf5; color: #047857;
      border-radius: 999px; font-family: ui-monospace, monospace;
      font-size: 1.5rem; font-weight: 700;
    }}
    .modal .body {{ color: #475569; font-size: .95rem; line-height: 1.5; margin: 0 0 1.25rem; }}
    .modal .close {{
      background: #1d4ed8; color: #fff; border: none;
      padding: .6rem 1.4rem; border-radius: 999px; font-weight: 600; cursor: pointer;
    }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Record simulated UAT result</h1>
    <p>UAT reference: <span class="ref">{safe_id}</span></p>
    <p>This simulates completing the on-site UAT and sending the result to the registrar system. Click only when you are ready to record your simulated score.</p>
    <button id="take-btn" type="button">Take test (simulate)</button>
  </div>

  <div class="overlay" id="overlay" role="dialog" aria-modal="true" aria-labelledby="modal-title">
    <div class="modal">
      <div class="icon" id="modal-icon">✓</div>
      <h2 id="modal-title">UAT recorded</h2>
      <div class="score" id="modal-score" style="display:none;"></div>
      <p class="body" id="modal-body"></p>
      <button class="close" id="close-btn" type="button">Close</button>
    </div>
  </div>

  <script>
    (function () {{
      const callbackUrl = "{callback_url}";
      const btn = document.getElementById('take-btn');
      const overlay = document.getElementById('overlay');
      const icon = document.getElementById('modal-icon');
      const title = document.getElementById('modal-title');
      const scoreEl = document.getElementById('modal-score');
      const body = document.getElementById('modal-body');
      const closeBtn = document.getElementById('close-btn');

      function showSuccess(score, message) {{
        icon.className = 'icon icon-ok';
        icon.textContent = '✓';
        title.textContent = 'UAT recorded successfully';
        scoreEl.textContent = score + ' / 100';
        scoreEl.style.display = 'inline-block';
        body.textContent = message || 'Your simulated UAT result has been sent to the registrar. You can close this window.';
        overlay.classList.add('is-open');
      }}

      function showError(message) {{
        icon.className = 'icon icon-err';
        icon.textContent = '!';
        title.textContent = 'Could not record UAT';
        scoreEl.style.display = 'none';
        body.textContent = message || 'Something went wrong. Please try again later or contact the registrar.';
        overlay.classList.add('is-open');
      }}

      closeBtn.addEventListener('click', () => overlay.classList.remove('is-open'));
      overlay.addEventListener('click', (e) => {{ if (e.target === overlay) overlay.classList.remove('is-open'); }});

      btn.addEventListener('click', async () => {{
        btn.disabled = true;
        btn.textContent = 'Submitting…';
        try {{
          const res = await fetch(callbackUrl, {{
            method: 'POST',
            headers: {{ 'Accept': 'application/json' }},
          }});
          const data = await res.json().catch(() => ({{}}));
          if (res.ok) {{
            showSuccess(data.score, data.message);
          }} else {{
            const detail = (data && (data.detail || data.message)) || ('Request failed (' + res.status + ')');
            showError(detail);
            btn.disabled = false;
            btn.textContent = 'Take test (simulate)';
          }}
        }} catch (err) {{
          showError('Network error. Please check your connection and try again.');
          btn.disabled = false;
          btn.textContent = 'Take test (simulate)';
        }}
      }});
    }})();
  </script>
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
