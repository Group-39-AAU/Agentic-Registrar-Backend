from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable, Sequence

from .schemas import RetrievedChunk, RetrievalResult


class EmbeddingProvider(ABC):
    """Interface for producing vector embeddings from text."""

    @abstractmethod
    async def embed_texts(self, texts: Iterable[str]) -> list[list[float]]:
        """Return embeddings for the given texts."""


class Retriever(ABC):
    """Interface for retrieving relevant chunks for a query."""

    @abstractmethod
    async def retrieve(self, query: str, *, limit: int = 5) -> RetrievalResult:
        """Retrieve relevant chunks for the given query."""


class IndexableDocument(ABC):
    """Simple interface representing content that can be indexed for retrieval."""

    @property
    @abstractmethod
    def id(self) -> str:  # pragma: no cover - trivial
        raise NotImplementedError

    @property
    @abstractmethod
    def text(self) -> str:  # pragma: no cover - trivial
        raise NotImplementedError

