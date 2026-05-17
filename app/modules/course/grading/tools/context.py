"""
Context-providing tools — breakdown integrity, roster composition,
deadline status, and historical baselines.

These augment the statistical tools with structural and policy
context so the LLM can reason "is this distribution unusual for
*this* course in *this* department?" rather than in a vacuum.
"""
from __future__ import annotations

import math
import uuid
from datetime import date
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.course.models import AcademicTerm, Course, Grade
from app.shared.enums import GradeSubmissionStatus


# Same tolerance the breakdown-CRUD path uses to validate sum==100.
_WEIGHT_SUM_TOLERANCE = 1e-6


def check_breakdown_integrity(
    components: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Verify the breakdown's sum-of-weights == 100 invariant, surface
    component count, and list each component's (name, weight,
    max_score) so the LLM sees the plan it's reviewing.

    Service-layer code already enforces this at write time; the tool
    re-runs the check at agent-review time as a defence in depth
    against direct DB edits.
    """
    weights = [c["weight"] for c in components]
    total = sum(weights) if weights else 0.0
    return {
        "component_count": len(components),
        "weight_sum": round(total, 6),
        "sums_to_100": math.isclose(
            total, 100.0, abs_tol=_WEIGHT_SUM_TOLERANCE,
        ),
        "components": [
            {
                "name": c["name"],
                "weight": c["weight"],
                "max_score": c["max_score"],
            }
            for c in components
        ],
    }


def summarize_roster(
    *,
    total: int,
    original_count: int,
    added_count: int,
) -> dict[str, Any]:
    """
    Compact roster composition for the LLM. The split between
    ``original_count`` (cohort members) and ``added_count`` (joined
    via add/drop) is useful context — a section heavily reshaped by
    add/drop has a different identity than a clean cohort.
    """
    return {
        "total": total,
        "original_count": original_count,
        "added_via_drop_count": added_count,
        "is_small_class": total < 10,
    }


def check_deadline_status(
    *,
    submitted_at,
    term_end_date: date,
    today: date,
) -> dict[str, Any]:
    """
    How far before / after the term's end date was this batch
    submitted? Positive ``days_before_end`` = early submission,
    negative = past the deadline.

    Track B's `validateDeadlineCompliance` check (checklist line 152)
    is one signal among many — late submission alone isn't an
    anomaly worth FLAGGING, but combined with other patterns it
    might be. The LLM decides.
    """
    days_before_end = (term_end_date - submitted_at.date()).days
    is_late = submitted_at.date() > term_end_date
    return {
        "submitted_at": submitted_at.isoformat(),
        "term_end_date": term_end_date.isoformat(),
        "today": today.isoformat(),
        "days_before_end": days_before_end,
        "is_late": is_late,
    }


async def compare_to_section_history(
    db: AsyncSession,
    *,
    course_id: uuid.UUID,
    current_term_id: uuid.UUID,
) -> dict[str, Any]:
    """
    Look back at all prior AUTHORISED ``Grade`` rows for this course
    (across every section and term) and compute the historical mean
    and pass rate. The LLM uses this baseline to judge whether the
    current batch is drifting unusually from the course's track
    record.

    Returns ``{"has_history": False, ...}`` when no past authorised
    grades exist — the course is new, or this is the first cohort.
    The LLM is told to reason without that signal in that case.
    """
    rows = (
        await db.execute(
            select(Grade).where(
                Grade.course_id == course_id,
                Grade.term_id != current_term_id,
                Grade.status == GradeSubmissionStatus.AUTHORISED,
                Grade.numeric_score.is_not(None),
                Grade.is_deleted == False,  # noqa: E712
            )
        )
    ).scalars().all()
    if not rows:
        return {
            "has_history": False,
            "prior_term_count": 0,
            "prior_student_count": 0,
            "prior_mean": None,
            "prior_pass_rate": None,
        }
    scores = [r.numeric_score for r in rows if r.numeric_score is not None]
    pass_count = sum(1 for s in scores if s >= 50.0)
    prior_terms = {r.term_id for r in rows}
    return {
        "has_history": True,
        "prior_term_count": len(prior_terms),
        "prior_student_count": len(scores),
        "prior_mean": round(sum(scores) / len(scores), 4),
        "prior_pass_rate": round(pass_count / len(scores), 4),
    }
