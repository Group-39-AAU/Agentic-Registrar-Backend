"""
Numeric → ``GradeLetter`` mapping for the AAU undergraduate scale.

The instructor enters per-component raw scores; submit time computes
the weighted-percent total (a number in [0, 100]) and maps it to a
letter via :func:`letter_for_numeric`. The reverse mapping
(letter → 4.0-scale points) lives in
:mod:`app.modules.course.grade_points` and is used by Track A's
CGPA calculator.

Cutoffs follow **AAU Senate Legislation Article 90.1** verbatim:

    [90, 100] → A+   (4.00, Excellent / First class with Great distinction)
    [83, 90)  → A    (4.00, Excellent)
    [80, 83)  → A-   (3.75)
    [75, 80)  → B+   (3.50, Very Good / First class with distinction)
    [68, 75)  → B    (3.00, Good / Second class)
    [65, 68)  → B-   (2.75)
    [60, 65)  → C+   (2.50, Satisfactory)
    [50, 60)  → C    (2.00)
    [45, 50)  → C-   (1.75, Unsatisfactory / Low class)
    [40, 45)  → D    (1.00, Very Poor)
    [<40]     → F    (0.00, Fail)

Boundaries are inclusive on the lower side (a flat 90.0 is A+, a flat
83.0 is A) and the mapping is total over [0, 100]. The administrative
marks I, NG, W, DO, P are recorded by separate paths (Art 90.7) and
never come out of this function.
"""
from __future__ import annotations

from app.shared.enums import GradeLetter


# Pairs of (lower bound inclusive, letter), ordered HIGH → LOW so the
# first match is the highest-applicable letter.
_LETTER_CUTOFFS: list[tuple[float, GradeLetter]] = [
    (90.0, GradeLetter.A_PLUS),
    (83.0, GradeLetter.A),
    (80.0, GradeLetter.A_MINUS),
    (75.0, GradeLetter.B_PLUS),
    (68.0, GradeLetter.B),
    (65.0, GradeLetter.B_MINUS),
    (60.0, GradeLetter.C_PLUS),
    (50.0, GradeLetter.C),
    (45.0, GradeLetter.C_MINUS),
    (40.0, GradeLetter.D),
    (0.0,  GradeLetter.F),
]


def letter_for_numeric(numeric_score: float) -> GradeLetter:
    """
    Map a 0–100 numeric score to its AAU letter grade per Senate Art 90.1.

    Scores marginally outside [0, 100] (e.g. 100.0001 from float
    rounding of the weighted sum) clamp to A+; negative scores clamp
    to F. Callers should already have validated component scores
    against ``max_score`` before getting here; this is a defence in
    depth rather than the primary guard.
    """
    if numeric_score >= 100:
        return GradeLetter.A_PLUS
    for lower, letter in _LETTER_CUTOFFS:
        if numeric_score >= lower:
            return letter
    return GradeLetter.F
