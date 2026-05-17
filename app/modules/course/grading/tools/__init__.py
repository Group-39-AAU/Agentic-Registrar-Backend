"""
Track B grading-monitor — deterministic tool layer.

Every function here is a **pure context provider** for the LLM
reasoner. Tools compute facts (means, stddevs, distributions,
outlier candidates, missing-cell lists, history comparisons). They
do NOT decide, flag, or produce verdicts — that's the LLM's job.

The agent calls these in parallel, packs the outputs into a single
JSON-serialisable dict, and hands it to the LLM as evidence. The
LLM then reasons as a department head and produces the APPROVE /
FLAG verdict plus flags + plain-English explanation.

Why this split?

The same statistical fact has different meanings in different
contexts. A 2σ outlier downward might be a struggling student or
a clerical entry error; a 60% pass rate might be cheating, a
brutally hard exam, or just a tough cohort. Only judgment can tell.
We give the LLM the numbers so it doesn't have to do arithmetic;
the LLM gives us the interpretation.

Module map:

  ``statistics``   — class-wide and per-component aggregates.
  ``anomalies``    — outlier candidates, identical-score clusters,
                     missing-cell detection. Note: the tool *finds*
                     candidates; it never declares them anomalies.
  ``context``      — breakdown integrity, roster composition,
                     historical baseline (where available),
                     deadline status.
"""
from app.modules.course.grading.tools.statistics import (
    compute_class_statistics,
    compute_component_statistics,
    compute_letter_distribution,
)
from app.modules.course.grading.tools.anomalies import (
    find_identical_score_clusters,
    list_missing_entries,
    list_score_outliers,
)
from app.modules.course.grading.tools.context import (
    check_breakdown_integrity,
    compare_to_section_history,
    check_deadline_status,
    summarize_roster,
)

__all__ = [
    "compute_class_statistics",
    "compute_component_statistics",
    "compute_letter_distribution",
    "find_identical_score_clusters",
    "list_missing_entries",
    "list_score_outliers",
    "check_breakdown_integrity",
    "compare_to_section_history",
    "check_deadline_status",
    "summarize_roster",
]
