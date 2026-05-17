"""
Numeric → ``GradeLetter`` mapping for the AAU undergraduate scale.

The instructor enters per-component raw scores; submit time computes
the weighted-percent total (a number in [0, 100]) and maps it to a
letter via :func:`letter_for_numeric`. The reverse mapping
(letter → 4.0-scale points) lives in
:mod:`app.modules.course.grade_points` and is used by Track A's
CGPA calculator.

Cutoffs follow the AAU Senate undergraduate scale:

    score >= 90 → A
    score >= 85 → A-
    score >= 80 → B+
    score >= 75 → B
    score >= 70 → B-
    score >= 65 → C+
    score >= 60 → C
    score >= 55 → C-
    score >= 50 → D
    score <  50 → F

The boundaries are inclusive on the lower side (a flat 90.0 is an A,
not an A-), and the mapping is total over [0, 100] — there is no
gap, no ambiguity. ``I`` and ``NG`` are administrative outcomes
recorded by a separate path; they never come out of this function.
"""
from __future__ import annotations

from app.shared.enums import GradeLetter


# Pairs of (lower bound inclusive, letter), ordered HIGH → LOW so the
# first match is the highest-applicable letter. A list keeps the
# ordering stable and makes the mapping easy to audit at a glance.
_LETTER_CUTOFFS: list[tuple[float, GradeLetter]] = [
    (90.0, GradeLetter.A),
    (85.0, GradeLetter.A_MINUS),
    (80.0, GradeLetter.B_PLUS),
    (75.0, GradeLetter.B),
    (70.0, GradeLetter.B_MINUS),
    (65.0, GradeLetter.C_PLUS),
    (60.0, GradeLetter.C),
    (55.0, GradeLetter.C_MINUS),
    (50.0, GradeLetter.D),
    (0.0,  GradeLetter.F),
]


def letter_for_numeric(numeric_score: float) -> GradeLetter:
    """
    Map a 0–100 numeric score to its AAU letter grade.

    Scores marginally outside [0, 100] (e.g. 100.0001 from float
    rounding of the weighted sum) clamp to A; negative scores clamp
    to F. Callers should already have validated component scores
    against ``max_score`` before getting here; this is a defence in
    depth rather than the primary guard.
    """
    if numeric_score >= 100:
        return GradeLetter.A
    for lower, letter in _LETTER_CUTOFFS:
        if numeric_score >= lower:
            return letter
    return GradeLetter.F
