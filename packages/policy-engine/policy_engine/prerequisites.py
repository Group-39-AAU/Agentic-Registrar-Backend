from __future__ import annotations

from typing import Iterable, Set


def has_completed_prerequisites(
    completed_courses: Iterable[str],
    required_prerequisites: Iterable[str],
) -> bool:
    """Return True if all required prerequisites are contained in completed courses."""

    completed_set: Set[str] = {c.lower() for c in completed_courses}
    required_set: Set[str] = {c.lower() for c in required_prerequisites}
    return required_set.issubset(completed_set)


def missing_prerequisites(
    completed_courses: Iterable[str],
    required_prerequisites: Iterable[str],
) -> set[str]:
    """Return the set of missing prerequisites."""

    completed_set = {c.lower() for c in completed_courses}
    required_set = {c.lower() for c in required_prerequisites}
    return {c for c in required_set if c not in completed_set}

