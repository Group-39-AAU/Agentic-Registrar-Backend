from __future__ import annotations

from typing import Any, Callable

import pytest


def mark_integration(func: Callable[..., Any]) -> Callable[..., Any]:
    """Mark a test as an integration test."""

    return pytest.mark.integration(func)


def mark_slow(func: Callable[..., Any]) -> Callable[..., Any]:
    """Mark a test as slow."""

    return pytest.mark.slow(func)


def parametrize_ids(values: list[Any]) -> list[str]:
    """Generate deterministic pytest IDs from a list of values."""

    return [str(v) for v in values]

