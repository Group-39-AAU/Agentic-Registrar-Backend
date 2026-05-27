"""
Grade-points lookup — pure-function tests.

No fixtures, no DB, no I/O. Covers every branch of
``points_for``, ``is_passing``, and ``counts_toward_cgpa``
against the AAU Senate 4.0 scale (Art 90.1).
"""
from __future__ import annotations

import pytest

from app.modules.course.grade_points import (
    counts_toward_cgpa,
    is_passing,
    points_for,
)
from app.shared.enums import GradeLetter


# ── points_for ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("letter", "expected"),
    [
        (GradeLetter.A_PLUS,  4.00),
        (GradeLetter.A,       4.00),
        (GradeLetter.A_MINUS, 3.75),
        (GradeLetter.B_PLUS,  3.50),
        (GradeLetter.B,       3.00),
        (GradeLetter.B_MINUS, 2.75),
        (GradeLetter.C_PLUS,  2.50),
        (GradeLetter.C,       2.00),
        (GradeLetter.C_MINUS, 1.75),
        (GradeLetter.D,       1.00),
        (GradeLetter.F,       0.00),
    ],
)
def test_points_for_numeric_letters(letter, expected):
    assert points_for(letter) == expected


@pytest.mark.parametrize(
    "letter",
    [GradeLetter.I, GradeLetter.NG, GradeLetter.W, GradeLetter.DO, GradeLetter.P],
)
def test_points_for_administrative_marks_returns_none(letter):
    """I, NG, W, DO, P carry no GPA point value per Senate Art 90.7."""
    assert points_for(letter) is None


def test_a_plus_and_a_share_the_same_points():
    """A+ and A both map to 4.00 — the distinction is description only."""
    assert points_for(GradeLetter.A_PLUS) == points_for(GradeLetter.A) == 4.00


# ── is_passing ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "letter",
    [
        GradeLetter.A_PLUS, GradeLetter.A, GradeLetter.A_MINUS,
        GradeLetter.B_PLUS, GradeLetter.B, GradeLetter.B_MINUS,
        GradeLetter.C_PLUS, GradeLetter.C, GradeLetter.C_MINUS,
        GradeLetter.D,
    ],
)
def test_is_passing_true_for_d_and_above(letter):
    assert is_passing(letter) is True


def test_is_passing_f_is_false():
    assert is_passing(GradeLetter.F) is False


@pytest.mark.parametrize(
    "letter",
    [GradeLetter.I, GradeLetter.NG, GradeLetter.W, GradeLetter.DO, GradeLetter.P],
)
def test_is_passing_administrative_marks_are_false(letter):
    assert is_passing(letter) is False


def test_is_passing_none_is_false():
    assert is_passing(None) is False


# ── counts_toward_cgpa ────────────────────────────────────────


@pytest.mark.parametrize(
    "letter",
    [
        GradeLetter.A_PLUS, GradeLetter.A, GradeLetter.A_MINUS,
        GradeLetter.B_PLUS, GradeLetter.B, GradeLetter.B_MINUS,
        GradeLetter.C_PLUS, GradeLetter.C, GradeLetter.C_MINUS,
        GradeLetter.D,
        GradeLetter.F,   # F contributes 0.0 × credits — still counted
    ],
)
def test_counts_toward_cgpa_true_for_numeric_letters(letter):
    assert counts_toward_cgpa(letter) is True


@pytest.mark.parametrize(
    "letter",
    [GradeLetter.I, GradeLetter.NG, GradeLetter.W, GradeLetter.DO, GradeLetter.P],
)
def test_counts_toward_cgpa_false_for_administrative_marks(letter):
    """Senate Art 90.7.4 and 90.7.6 exclude these from GPA computation."""
    assert counts_toward_cgpa(letter) is False


def test_counts_toward_cgpa_none_is_false():
    assert counts_toward_cgpa(None) is False


def test_f_counts_toward_cgpa_but_is_not_passing():
    """F lowers the GPA (counts as 0.0) but does not satisfy a course requirement."""
    assert counts_toward_cgpa(GradeLetter.F) is True
    assert is_passing(GradeLetter.F) is False
