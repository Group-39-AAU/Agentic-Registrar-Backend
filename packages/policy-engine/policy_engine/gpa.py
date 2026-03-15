from __future__ import annotations

from typing import Iterable, Sequence


def calculate_gpa(grades: Sequence[float], credits: Sequence[float]) -> float:
    """Calculate GPA for a single term.

    `grades` and `credits` must be the same length. GPA is the weighted average of
    grades by credits.
    """

    if len(grades) != len(credits):
        raise ValueError("grades and credits must have the same length")
    total_credits = sum(credits)
    if total_credits <= 0:
        raise ValueError("total credits must be positive")
    total_points = sum(g * c for g, c in zip(grades, credits))
    return total_points / total_credits


def calculate_cgpa(term_gpas: Iterable[float], term_credits: Iterable[float]) -> float:
    """Calculate cumulative GPA across multiple terms."""

    gpas = list(term_gpas)
    credits = list(term_credits)
    if len(gpas) != len(credits):
        raise ValueError("term_gpas and term_credits must have the same length")
    total_credits = sum(credits)
    if total_credits <= 0:
        raise ValueError("total credits must be positive")
    total_points = sum(g * c for g, c in zip(gpas, credits))
    return total_points / total_credits

