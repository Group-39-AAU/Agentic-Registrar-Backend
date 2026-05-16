"""
GradeLetter → 4.0-scale grade-points mapping.

Mirrors the AAU Senate grading scale used by the Academic Standing
Agent's CGPA calculator (SDS Table 75). I and NG are sentinels with
no point value — they are excluded from CGPA and routed through the
``handleEdgeCase`` officer-review path instead of being computed.
"""

from __future__ import annotations

from typing import Optional

from app.shared.enums import GradeLetter


# AAU Senate 4.0 scale.
GRADE_POINTS: dict[GradeLetter, float] = {
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
    # I and NG intentionally absent — see module docstring.
}


# Letters that count as "passed" for the purpose of completed-course
# resolution and prerequisite satisfaction. A D is the lowest passing
# grade in the AAU undergraduate scale.
PASSING_LETTERS: frozenset[GradeLetter] = frozenset({
    GradeLetter.A, GradeLetter.A_MINUS,
    GradeLetter.B_PLUS, GradeLetter.B, GradeLetter.B_MINUS,
    GradeLetter.C_PLUS, GradeLetter.C, GradeLetter.C_MINUS,
    GradeLetter.D,
})


def points_for(letter: GradeLetter) -> Optional[float]:
    """Look up the 4.0-scale points for a letter, or None for I/NG."""
    return GRADE_POINTS.get(letter)


def is_passing(letter: Optional[GradeLetter]) -> bool:
    """True when the letter counts as a passed course."""
    return letter is not None and letter in PASSING_LETTERS


def counts_toward_cgpa(letter: Optional[GradeLetter]) -> bool:
    """
    True when the letter has a 4.0-scale point value. F counts (it
    contributes 0.0 × credit_hours); I and NG do not.
    """
    return letter is not None and letter in GRADE_POINTS
