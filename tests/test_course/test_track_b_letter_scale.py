"""
Track B PR 2 — numeric → AAU letter mapping.

Pure-function tests for ``letter_for_numeric``. No fixtures, no DB —
this is the trivially-fast tier.
"""
from __future__ import annotations

import pytest

from app.modules.course.grading.letter_scale import letter_for_numeric
from app.shared.enums import GradeLetter


@pytest.mark.parametrize(
    ("score", "letter"),
    [
        (100.0, GradeLetter.A),
        (95.0,  GradeLetter.A),
        (90.0,  GradeLetter.A),           # boundary
        (89.99, GradeLetter.A_MINUS),
        (85.0,  GradeLetter.A_MINUS),     # boundary
        (84.99, GradeLetter.B_PLUS),
        (80.0,  GradeLetter.B_PLUS),
        (75.0,  GradeLetter.B),
        (70.0,  GradeLetter.B_MINUS),
        (65.0,  GradeLetter.C_PLUS),
        (60.0,  GradeLetter.C),
        (55.0,  GradeLetter.C_MINUS),
        (50.0,  GradeLetter.D),
        (49.99, GradeLetter.F),
        (0.0,   GradeLetter.F),
    ],
)
def test_letter_mapping_at_and_around_boundaries(score, letter):
    assert letter_for_numeric(score) is letter


def test_clamp_above_100_returns_a():
    """Float rounding can push the weighted sum to 100.0000001 — that's still A."""
    assert letter_for_numeric(100.000001) is GradeLetter.A


def test_negative_returns_f():
    assert letter_for_numeric(-1.0) is GradeLetter.F
