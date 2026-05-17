"""
Deterministic statistical context tools for the GradingMonitorAgent.

Every function here is pure (no I/O, no decisions) and returns a
JSON-serialisable dict the LLM can read. Inputs are already-resolved
in-memory values — the agent does the SQL once and passes the
hydrated payload to every tool.

Contract: tools compute **facts**, never opinions. "mean is 71.4" is
a fact. "the class is failing" is an opinion — that's the LLM's job
to decide based on the facts.
"""
from __future__ import annotations

import statistics as stdlib_stats
from typing import Any

from app.modules.course.grade_points import is_passing
from app.shared.enums import GradeLetter


def compute_class_statistics(
    numeric_scores: list[float],
) -> dict[str, Any]:
    """
    Aggregate statistics across the final per-student numeric scores
    in [0, 100]. The LLM uses these to judge distribution shape,
    pass rate, and spread.

    Pass rate uses the AAU passing cutoff (score >= 50, i.e. letter
    D or better). Returns ``None`` values for stddev/median when the
    sample is too small to compute them — the LLM is told that a
    tiny class makes statistical anomalies less meaningful.
    """
    n = len(numeric_scores)
    if n == 0:
        return {
            "n": 0, "mean": None, "median": None, "stddev": None,
            "min": None, "max": None,
            "pass_count": 0, "fail_count": 0, "pass_rate": None,
        }

    mean = stdlib_stats.fmean(numeric_scores)
    median = stdlib_stats.median(numeric_scores)
    stddev = (
        stdlib_stats.pstdev(numeric_scores)
        if n >= 2 else 0.0
    )
    pass_count = sum(1 for s in numeric_scores if s >= 50.0)
    fail_count = n - pass_count

    return {
        "n": n,
        "mean": round(mean, 4),
        "median": round(median, 4),
        "stddev": round(stddev, 4),
        "min": round(min(numeric_scores), 4),
        "max": round(max(numeric_scores), 4),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "pass_rate": round(pass_count / n, 4),
    }


def compute_component_statistics(
    component_data: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Per-component descriptive stats so the LLM can pinpoint *which*
    assessment is driving the overall picture.

    ``component_data`` shape:
        [{"name": str, "weight": float, "max_score": float,
          "raw_scores": list[float]}, ...]

    Returns one entry per component with mean/stddev/min/max of the
    raw scores normalised to a 0–100 percentage scale (so the LLM
    can compare across components with different max_scores without
    doing arithmetic).
    """
    out: list[dict[str, Any]] = []
    for c in component_data:
        raw = c["raw_scores"]
        max_score = c["max_score"]
        # Normalise to percentage of the component's max so the
        # numbers are comparable across components.
        pct = [(r / max_score) * 100.0 for r in raw] if max_score else []
        n = len(pct)
        out.append({
            "name": c["name"],
            "weight": c["weight"],
            "max_score": max_score,
            "n": n,
            "mean_pct": round(stdlib_stats.fmean(pct), 4) if n else None,
            "stddev_pct": (
                round(stdlib_stats.pstdev(pct), 4) if n >= 2 else 0.0
            ),
            "min_pct": round(min(pct), 4) if n else None,
            "max_pct": round(max(pct), 4) if n else None,
        })
    return out


def compute_letter_distribution(
    letter_grades: list[GradeLetter],
) -> dict[str, int]:
    """
    Histogram of letter grades. The LLM uses this to assess
    distribution shape (uniform / bimodal / heavy at one end). Keys
    follow the canonical enum *value* strings (e.g. "A-", "B+") so
    the LLM sees them in human-readable form.

    Every defined letter appears in the result, even with count 0,
    so the LLM doesn't have to know which letters were "missing" vs
    "uncounted".
    """
    histogram: dict[str, int] = {letter.value: 0 for letter in GradeLetter}
    for letter in letter_grades:
        histogram[letter.value] = histogram.get(letter.value, 0) + 1
    return histogram
