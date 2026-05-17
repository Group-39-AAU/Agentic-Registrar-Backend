"""
Track B PR 3 — deterministic tool layer unit tests.

These exercise the pure-function tools the GradingMonitorAgent
relies on. No DB, no LLM — just data in, data out. Whatever the
agent later does with the numbers, the numbers themselves have to
be right.
"""
from __future__ import annotations

import pytest

from app.modules.course.grading.tools import (
    check_breakdown_integrity, check_deadline_status,
    compute_class_statistics, compute_component_statistics,
    compute_letter_distribution, find_identical_score_clusters,
    list_missing_entries, list_score_outliers, summarize_roster,
)
from app.modules.course.grading.tools.anomalies import DEFAULT_Z_THRESHOLD
from app.shared.enums import GradeLetter
from datetime import date, datetime, timezone


# ── statistics.compute_class_statistics ─────────────────────────


def test_class_stats_basic():
    s = compute_class_statistics([100.0, 80.0, 60.0, 40.0, 20.0])
    assert s["n"] == 5
    assert s["mean"] == 60.0
    assert s["median"] == 60.0
    assert s["min"] == 20.0
    assert s["max"] == 100.0
    assert s["pass_count"] == 3   # 100, 80, 60 are >= 50
    assert s["fail_count"] == 2
    assert s["pass_rate"] == 0.6
    assert s["stddev"] > 0


def test_class_stats_empty():
    s = compute_class_statistics([])
    assert s["n"] == 0
    assert s["mean"] is None
    assert s["stddev"] is None
    assert s["pass_rate"] is None


def test_class_stats_singleton():
    s = compute_class_statistics([75.0])
    assert s["n"] == 1
    assert s["mean"] == 75.0
    assert s["stddev"] == 0.0  # n=1 → stddev defined as 0 here
    assert s["pass_count"] == 1


# ── statistics.compute_letter_distribution ──────────────────────


def test_letter_distribution_every_letter_in_output():
    """Every enum value is keyed, even when count is 0."""
    histogram = compute_letter_distribution([
        GradeLetter.A, GradeLetter.A, GradeLetter.B_MINUS, GradeLetter.F,
    ])
    assert histogram["A"] == 2
    assert histogram["B-"] == 1
    assert histogram["F"] == 1
    assert histogram["C"] == 0  # uncounted but present
    assert set(histogram.keys()) == {letter.value for letter in GradeLetter}


# ── statistics.compute_component_statistics ─────────────────────


def test_component_stats_normalises_to_percent():
    """Mid-term out of 50 with a 30 raw score is 60% — that's what the LLM sees."""
    out = compute_component_statistics([
        {"name": "Mid", "weight": 30, "max_score": 50, "raw_scores": [30.0, 40.0, 50.0]},
    ])
    assert len(out) == 1
    assert out[0]["mean_pct"] == 80.0  # mean of 60, 80, 100


def test_component_stats_empty_raw_scores():
    out = compute_component_statistics([
        {"name": "Final", "weight": 40, "max_score": 100, "raw_scores": []},
    ])
    assert out[0]["mean_pct"] is None
    assert out[0]["n"] == 0


# ── anomalies.list_score_outliers ───────────────────────────────


def test_outliers_too_few_students():
    """n < 3 → cannot compute meaningful z-score."""
    out = list_score_outliers([
        {"student_number": "UGR/01/15", "full_name": "A", "numeric_score": 80},
        {"student_number": "UGR/02/15", "full_name": "B", "numeric_score": 90},
    ])
    assert out["below"] == []
    assert out["above"] == []
    assert out["stddev"] is None
    assert "note" in out


def test_outliers_zero_stddev_returns_note():
    """Everyone got the same numeric score → z-score undefined."""
    students = [
        {"student_number": f"UGR/0{i}/15", "full_name": f"S{i}", "numeric_score": 85.0}
        for i in range(1, 6)
    ]
    out = list_score_outliers(students)
    assert out["stddev"] == 0.0
    assert "note" in out
    assert out["below"] == []
    assert out["above"] == []


def test_outliers_surfaces_high_and_low():
    """One genuine downward outlier among a tight cluster."""
    students = [
        {"student_number": f"UGR/0{i}/15", "full_name": f"S{i}", "numeric_score": 80.0}
        for i in range(1, 10)
    ] + [
        {"student_number": "UGR/99/15", "full_name": "Sad", "numeric_score": 10.0},
    ]
    out = list_score_outliers(students, z_threshold=2.0)
    assert any(s["student_number"] == "UGR/99/15" for s in out["below"])
    assert out["above"] == []


# ── anomalies.find_identical_score_clusters ─────────────────────


def test_identical_clusters_basic():
    out = find_identical_score_clusters([85, 85, 85, 90, 90, 70])
    by_score = {c["score"]: c["count"] for c in out["clusters"]}
    assert by_score == {85.0: 3, 90.0: 2}
    assert out["total_clusters"] == 2


def test_identical_clusters_no_repeats():
    out = find_identical_score_clusters([10, 20, 30, 40, 50])
    assert out["clusters"] == []
    assert out["total_clusters"] == 0


# ── anomalies.list_missing_entries ──────────────────────────────


def test_missing_entries_complete_batch():
    out = list_missing_entries(
        roster_student_ids=["s1", "s2"],
        component_names=["Quiz", "Final"],
        score_map={
            ("s1", "Quiz"): 8, ("s1", "Final"): 90,
            ("s2", "Quiz"): 7, ("s2", "Final"): 85,
        },
    )
    assert out["any_missing"] is False
    assert out["missing_count"] == 0
    assert out["rows"] == []


def test_missing_entries_partial():
    out = list_missing_entries(
        roster_student_ids=["s1", "s2"],
        component_names=["Quiz", "Final"],
        score_map={
            ("s1", "Quiz"): 8, ("s1", "Final"): 90,
            ("s2", "Quiz"): 7, ("s2", "Final"): None,  # missing
        },
    )
    assert out["any_missing"] is True
    assert out["missing_count"] == 1
    assert out["rows"] == [
        {"student_id": "s2", "missing_components": ["Final"]},
    ]


# ── context.check_breakdown_integrity ───────────────────────────


def test_breakdown_integrity_sum_100():
    out = check_breakdown_integrity([
        {"name": "Mid", "weight": 30, "max_score": 50},
        {"name": "Final", "weight": 70, "max_score": 100},
    ])
    assert out["component_count"] == 2
    assert out["sums_to_100"] is True


def test_breakdown_integrity_sum_off():
    out = check_breakdown_integrity([
        {"name": "X", "weight": 60, "max_score": 100},
    ])
    assert out["sums_to_100"] is False


# ── context.summarize_roster ────────────────────────────────────


def test_summarize_roster_small_class_flag():
    out = summarize_roster(total=8, original_count=7, added_count=1)
    assert out["is_small_class"] is True
    out = summarize_roster(total=42, original_count=40, added_count=2)
    assert out["is_small_class"] is False


# ── context.check_deadline_status ───────────────────────────────


def test_deadline_status_on_time():
    out = check_deadline_status(
        submitted_at=datetime(2026, 12, 1, 12, 0, tzinfo=timezone.utc),
        term_end_date=date(2027, 1, 31),
        today=date(2026, 12, 1),
    )
    assert out["is_late"] is False
    assert out["days_before_end"] > 0


def test_deadline_status_late():
    out = check_deadline_status(
        submitted_at=datetime(2027, 2, 5, 12, 0, tzinfo=timezone.utc),
        term_end_date=date(2027, 1, 31),
        today=date(2027, 2, 5),
    )
    assert out["is_late"] is True
    assert out["days_before_end"] < 0
