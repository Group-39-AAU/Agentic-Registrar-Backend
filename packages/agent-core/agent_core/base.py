from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Generic, Mapping, TypeVar

StateT = TypeVar("StateT")
ResultT = TypeVar("ResultT")


class BaseAgent(ABC, Generic[StateT, ResultT]):
    """Base abstraction for an agent participating in a workflow graph."""

    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    async def run(self, state: StateT, /, **kwargs: Any) -> ResultT:
        """Execute this agent against the given state."""


class ContextAwareAgent(BaseAgent[StateT, ResultT], ABC):
    """Agent that can receive shared execution context metadata."""

    @abstractmethod
    async def run_with_context(
        self,
        state: StateT,
        context: Mapping[str, Any],
        /,
        **kwargs: Any,
    ) -> ResultT:
        """Execute the agent with access to shared graph context."""

