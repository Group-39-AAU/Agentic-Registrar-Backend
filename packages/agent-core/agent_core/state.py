from __future__ import annotations

from typing import Any, Dict, Generic, Mapping, MutableMapping, TypeVar

from pydantic import BaseModel, Field


class GraphContext(BaseModel):
    """Lightweight, serialisable context shared across graph steps."""

    correlation_id: str | None = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


StateT = TypeVar("StateT", bound=Mapping[str, Any])


class GraphState(BaseModel, Generic[StateT]):
    """Base type for LangGraph-compatible state objects.

    The concrete services can extend this with domain-specific fields while
    preserving a consistent shape for history and context.
    """

    data: StateT
    context: GraphContext = Field(default_factory=GraphContext)
    history: list[dict[str, Any]] = Field(default_factory=list)


def merge_state(current: StateT, update: Mapping[str, Any]) -> dict[str, Any]:
    """Merge a partial update into the current graph state."""

    merged: Dict[str, Any] = dict(current)
    merged.update(update)
    return merged


def append_history_entry(
    history: list[dict[str, Any]],
    entry: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return a new history list with the additional entry appended."""

    new_history = list(history)
    new_history.append(dict(entry))
    return new_history

