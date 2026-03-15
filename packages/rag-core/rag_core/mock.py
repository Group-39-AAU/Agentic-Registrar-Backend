from __future__ import annotations

import math
from typing import Iterable, List

from .interfaces import EmbeddingProvider, Retriever
from .schemas import ChunkMetadata, RetrievedChunk, RetrievalResult


class InMemoryEmbeddingProvider(EmbeddingProvider):
    """Deterministic, low-quality embedding provider for testing.

    This implementation is intentionally simple and uses character-level hashing
    to produce small numeric vectors, suitable only for unit tests and local
    experiments.
    """

    def __init__(self, dim: int = 8) -> None:
        self.dim = dim

    async def embed_texts(self, texts: Iterable[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self.dim
            for i, ch in enumerate(text):
                vec[i % self.dim] += float(ord(ch))
            # L2 normalise
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            vectors.append([x / norm for x in vec])
        return vectors


class InMemoryRetriever(Retriever):
    """Very small in-memory retriever suitable for tests."""

    def __init__(self, provider: EmbeddingProvider) -> None:
        self._provider = provider
        self._ids: list[str] = []
        self._texts: list[str] = []
        self._metadatas: list[ChunkMetadata] = []
        self._embeddings: list[list[float]] = []

    async def add_document(self, *, doc_id: str, text: str, metadata: ChunkMetadata | None = None) -> None:
        self._ids.append(doc_id)
        self._texts.append(text)
        self._metadatas.append(metadata or ChunkMetadata())
        [embedding] = await self._provider.embed_texts([text])
        self._embeddings.append(embedding)

    async def retrieve(self, query: str, *, limit: int = 5) -> RetrievalResult:
        if not self._texts:
            return RetrievalResult(query=query, chunks=[])
        [query_vec] = await self._provider.embed_texts([query])
        scores: List[tuple[float, int]] = []
        for idx, emb in enumerate(self._embeddings):
            score = sum(a * b for a, b in zip(query_vec, emb))
            scores.append((score, idx))
        scores.sort(reverse=True, key=lambda s: s[0])
        chunks: list[RetrievedChunk] = []
        for score, idx in scores[:limit]:
            chunks.append(
                RetrievedChunk(
                    id=self._ids[idx],
                    text=self._texts[idx],
                    score=score,
                    metadata=self._metadatas[idx],
                )
            )
        return RetrievalResult(query=query, chunks=chunks)

