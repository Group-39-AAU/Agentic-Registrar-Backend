from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Iterable, Iterator, Sequence, TypeVar

T = TypeVar("T")


def utc_now() -> datetime:
    """Return a timezone-aware UTC `datetime`."""

    return datetime.now(tz=timezone.utc)


def generate_uuid() -> uuid.UUID:
    """Generate a random UUID4."""

    return uuid.uuid4()


def chunked(iterable: Iterable[T], size: int) -> Iterator[Sequence[T]]:
    """Yield items from *iterable* in chunks of the given size."""

    if size <= 0:
        raise ValueError("size must be positive")
    chunk: list[T] = []
    for item in iterable:
        chunk.append(item)
        if len(chunk) >= size:
            yield tuple(chunk)
            chunk.clear()
    if chunk:
        yield tuple(chunk)

