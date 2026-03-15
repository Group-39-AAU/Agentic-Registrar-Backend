from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")


class RetryError(Exception):
    """Raised when a retryable operation ultimately fails."""


def _compute_backoff(base: float, factor: float, attempt: int, jitter: bool) -> float:
    delay = base * (factor ** (attempt - 1))
    if jitter:
        delay *= random.uniform(0.5, 1.5)
    return delay


def retry(
    *,
    attempts: int = 3,
    base_delay: float = 0.1,
    factor: float = 2.0,
    jitter: bool = True,
    retriable_exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Synchronous retry decorator with exponential backoff."""

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            last_exc: BaseException | None = None
            for attempt in range(1, attempts + 1):
                try:
                    return func(*args, **kwargs)
                except retriable_exceptions as exc:  # type: ignore[misc]
                    last_exc = exc
                    if attempt >= attempts:
                        raise RetryError(f"Operation failed after {attempts} attempts") from exc
                    delay = _compute_backoff(base_delay, factor, attempt, jitter)
                    time.sleep(delay)
            assert last_exc is not None
            raise RetryError("Operation failed") from last_exc

        return wrapper

    return decorator


def async_retry(
    *,
    attempts: int = 3,
    base_delay: float = 0.1,
    factor: float = 2.0,
    jitter: bool = True,
    retriable_exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """Asynchronous retry decorator with exponential backoff."""

    def decorator(func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            last_exc: BaseException | None = None
            for attempt in range(1, attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except retriable_exceptions as exc:  # type: ignore[misc]
                    last_exc = exc
                    if attempt >= attempts:
                        raise RetryError(f"Operation failed after {attempts} attempts") from exc
                    delay = _compute_backoff(base_delay, factor, attempt, jitter)
                    await asyncio.sleep(delay)
            assert last_exc is not None
            raise RetryError("Operation failed") from last_exc

        return wrapper

    return decorator

