from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

from pydantic import BaseModel, Field


class ChunkMetadata(BaseModel):
    source: str | None = Field(default=None, description="Origin of the chunk (e.g. 'postgres').")
    source_id: str | None = Field(default=None, description="Identifier of the underlying record.")
    created_at: datetime | None = None
    updated_at: datetime | None = None
    tags: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class RetrievedChunk(BaseModel):
    id: str
    text: str
    score: float
    metadata: ChunkMetadata = Field(default_factory=ChunkMetadata)


class Citation(BaseModel):
    chunk_id: str
    offset_start: int | None = None
    offset_end: int | None = None


class RetrievalResult(BaseModel):
    query: str
    chunks: Sequence[RetrievedChunk]
    citations: Sequence[Citation] = Field(default_factory=list)

