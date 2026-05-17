"""
GradeLetter → 4.0-scale grade-points mapping.

Mirrors the AAU Senate grading scale (Article 90.1) used by the
Academic Standing Agent's CGPA calculator. The administrative marks
I, NG, W, DO, and P are sentinels with no point value — per Senate
Art 90.7.4 ("Neither 'W', 'DO', nor 'I' shall play any part in the
computation of the semester grade point average") and Art 90.7.6
(P/F non-credit work not included in GPA), they are excluded from
CGPA and routed through the ``handleEdgeCase`` officer-review path
where applicable.
"""

from __future__ import annotations

from typing import Optional

from app.shared.enums import GradeLetter


# AAU Senate 4.0 scale (Article 90.1). A+ and A both carry 4.00 —
# the difference is only the class description.
GRADE_POINTS: dict[GradeLetter, float] = {
    GradeLetter.A_PLUS:   4.00,
    GradeLetter.A:        4.00,
    GradeLetter.A_MINUS:  3.75,
    GradeLetter.B_PLUS:   3.50,
    GradeLetter.B:        3.00,
    GradeLetter.B_MINUS:  2.75,
    GradeLetter.C_PLUS:   2.50,
    GradeLetter.C:        2.00,
    GradeLetter.C_MINUS:  1.75,
    GradeLetter.D:        1.00,
    GradeLetter.F:        0.00,
    # I, NG, W, DO, P intentionally absent — see module docstring.
}


# Letters that count as "passed" for the purpose of completed-course
# resolution and prerequisite satisfaction. D is the lowest passing
# grade in the AAU undergraduate scale. Article 90.2 raises the bar
# to C for *graduation* (each required module must be ≥ C), but
# prerequisite satisfaction allows any non-F numeric letter.
PASSING_LETTERS: frozenset[GradeLetter] = frozenset({
    GradeLetter.A_PLUS, GradeLetter.A, GradeLetter.A_MINUS,
    GradeLetter.B_PLUS, GradeLetter.B, GradeLetter.B_MINUS,
    GradeLetter.C_PLUS, GradeLetter.C, GradeLetter.C_MINUS,
    GradeLetter.D,
})


def points_for(letter: GradeLetter) -> Optional[float]:
    """Look up the 4.0-scale points for a letter, or None for I/NG/W/DO/P."""
    return GRADE_POINTS.get(letter)


def is_passing(letter: Optional[GradeLetter]) -> bool:
    """True when the letter counts as a passed course."""
    return letter is not None and letter in PASSING_LETTERS


def counts_toward_cgpa(letter: Optional[GradeLetter]) -> bool:
    """
    True when the letter has a 4.0-scale point value. F counts (it
    contributes 0.0 × credit_hours). I, NG, W, DO, P do not — per
    Senate Art 90.7.4 and 90.7.6.
    """
    return letter is not None and letter in GRADE_POINTS
