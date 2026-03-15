from __future__ import annotations

from typing import Any, Mapping


def merge_metadata(base: Mapping[str, Any], update: Mapping[str, Any]) -> dict[str, Any]:
    """Merge two metadata mappings into a new dict."""

    merged = dict(base)
    merged.update(update)
    return merged


def shorten_id(value: str, length: int = 8) -> str:
    """Shorten a run or node identifier for logging or UI display."""

    if length <= 0:
        raise ValueError("length must be positive")
    return value[:length]

