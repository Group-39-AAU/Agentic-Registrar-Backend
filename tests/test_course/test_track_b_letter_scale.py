"""
Numeric → AAU letter mapping per Senate Article 90.1.

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
        # A+ band: [90, 100]
        (100.0, GradeLetter.A_PLUS),
        (95.0,  GradeLetter.A_PLUS),
        (90.0,  GradeLetter.A_PLUS),       # lower boundary
        # A band: [83, 90)
        (89.99, GradeLetter.A),
        (86.0,  GradeLetter.A),
        (83.0,  GradeLetter.A),            # lower boundary
        # A- band: [80, 83)
        (82.99, GradeLetter.A_MINUS),
        (80.0,  GradeLetter.A_MINUS),      # lower boundary
        # B+ band: [75, 80)
        (79.99, GradeLetter.B_PLUS),
        (75.0,  GradeLetter.B_PLUS),       # lower boundary
        # B band: [68, 75)
        (74.99, GradeLetter.B),
        (70.0,  GradeLetter.B),
        (68.0,  GradeLetter.B),            # lower boundary
        # B- band: [65, 68)
        (67.99, GradeLetter.B_MINUS),
        (65.0,  GradeLetter.B_MINUS),      # lower boundary
        # C+ band: [60, 65)
        (64.99, GradeLetter.C_PLUS),
        (60.0,  GradeLetter.C_PLUS),       # lower boundary
        # C band: [50, 60)
        (59.99, GradeLetter.C),
        (55.0,  GradeLetter.C),
        (50.0,  GradeLetter.C),            # lower boundary
        # C- band: [45, 50)
        (49.99, GradeLetter.C_MINUS),
        (45.0,  GradeLetter.C_MINUS),      # lower boundary
        # D band: [40, 45)
        (44.99, GradeLetter.D),
        (40.0,  GradeLetter.D),            # lower boundary
        # F band: < 40
        (39.99, GradeLetter.F),
        (0.0,   GradeLetter.F),
    ],
)
def test_letter_mapping_at_and_around_boundaries(score, letter):
    assert letter_for_numeric(score) is letter


def test_clamp_above_100_returns_a_plus():
    """Float rounding can push the weighted sum to 100.0000001 — that's still A+."""
    assert letter_for_numeric(100.000001) is GradeLetter.A_PLUS


def test_negative_returns_f():
    assert letter_for_numeric(-1.0) is GradeLetter.F
