"""
Anomaly *candidate* surfacing for the GradingMonitorAgent.

These tools find rows worth examining — they never declare anything
to be a problem. Whether a z=2.7 outlier is "a genuine top performer"
or "data-entry error" is judgment, and judgment is the LLM's job.

Each function returns a structured candidate list. An empty list
means "nothing worth surfacing", which the LLM can use as positive
evidence that the batch looks clean.
"""
from __future__ import annotations

import statistics as stdlib_stats
from collections import Counter
from typing import Any


# Default z-score cut-off for outlier surfacing. Track-B checklist
# line 150 names 2.5 standard deviations as the configurable default.
DEFAULT_Z_THRESHOLD: float = 2.5


def list_score_outliers(
    student_scores: list[dict[str, Any]],
    *,
    z_threshold: float = DEFAULT_Z_THRESHOLD,
) -> dict[str, Any]:
    """
    Compute z-score for every student's final numeric and surface
    those above ``|z| >= z_threshold``.

    ``student_scores`` shape:
        [{"student_number": str, "full_name": str,
          "numeric_score": float}, ...]

    Returns:
        {
          "z_threshold": float,
          "mean": float | None,
          "stddev": float | None,
          "below": [{...student, "z_score": float}, ...],  # downward
          "above": [{...student, "z_score": float}, ...],  # upward
        }

    With n < 3 the stddev is meaningless — we return empty candidate
    lists and tell the LLM ``stddev`` is None so it knows the test
    couldn't run.
    """
    n = len(student_scores)
    if n < 3:
        return {
            "z_threshold": z_threshold,
            "mean": None, "stddev": None,
            "below": [], "above": [],
            "note": (
                "Too few students for a meaningful z-score "
                "(need n >= 3)."
            ),
        }
    values = [s["numeric_score"] for s in student_scores]
    mean = stdlib_stats.fmean(values)
    stddev = stdlib_stats.pstdev(values)
    if stddev == 0:
        return {
            "z_threshold": z_threshold,
            "mean": round(mean, 4), "stddev": 0.0,
            "below": [], "above": [],
            "note": (
                "All students received the same numeric score — "
                "z-score is undefined. See identical_clusters."
            ),
        }

    below: list[dict[str, Any]] = []
    above: list[dict[str, Any]] = []
    for s in student_scores:
        z = (s["numeric_score"] - mean) / stddev
        if z <= -z_threshold:
            below.append({**s, "z_score": round(z, 4)})
        elif z >= z_threshold:
            above.append({**s, "z_score": round(z, 4)})
    return {
        "z_threshold": z_threshold,
        "mean": round(mean, 4),
        "stddev": round(stddev, 4),
        "below": sorted(below, key=lambda x: x["z_score"]),
        "above": sorted(above, key=lambda x: -x["z_score"]),
    }


def find_identical_score_clusters(
    numeric_scores: list[float],
    *,
    min_cluster_size: int = 2,
) -> dict[str, Any]:
    """
    Surface scores that multiple students share, sorted by frequency.

    Useful evidence for the LLM: an "all 85" cluster of 12 students
    might be (a) a binary attendance component graded the same way
    for everyone, (b) a copy-paste mistake, or (c) genuine
    coincidence. The LLM decides — we just surface the pattern.

    A cluster of size 1 is by definition not "identical", so the
    default threshold is 2.
    """
    counts = Counter(numeric_scores)
    clusters = [
        {"score": round(score, 4), "count": count}
        for score, count in counts.items()
        if count >= min_cluster_size
    ]
    clusters.sort(key=lambda c: (-c["count"], c["score"]))
    return {
        "min_cluster_size": min_cluster_size,
        "total_clusters": len(clusters),
        "clusters": clusters,
    }


def list_missing_entries(
    roster_student_ids: list[str],
    component_names: list[str],
    score_map: dict[tuple[str, str], float | None],
) -> dict[str, Any]:
    """
    Identify (student × component) cells with no score entered.

    The submit endpoint refuses an incomplete batch before the agent
    runs, so in the normal submit path this list is empty. The tool
    still exists for cases where the agent is invoked on a partial
    batch (e.g. a future deadline-monitor flow) or where data drifted
    between submit and agent run (a student added the section
    mid-grading).

    ``score_map`` is keyed by ``(student_id_str, component_name)`` so
    the agent can pass already-resolved tuples without exposing UUIDs
    to the LLM (UUIDs are not useful evidence).
    """
    missing: list[dict[str, Any]] = []
    for sid in roster_student_ids:
        gaps = [
            cname for cname in component_names
            if score_map.get((sid, cname)) is None
        ]
        if gaps:
            missing.append({
                "student_id": sid,
                "missing_components": gaps,
            })
    return {
        "any_missing": bool(missing),
        "missing_count": len(missing),
        "rows": missing,
    }
