"""
In-process event bus — unit tests.

Verifies the subscribe/publish contract defined in app.shared.events:
  - A registered handler is called when its event type is published.
  - Multiple handlers all fire for the same event.
  - Extra kwargs (e.g. db session) are forwarded to handlers.
  - Publishing to a type with no subscribers does not raise.
  - A failing handler does not prevent subsequent handlers from running.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.shared.events import DomainEvent, _handlers, publish, subscribe


# ── Test isolation ────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clear_bus():
    """Reset the global registry before and after every test."""
    _handlers.clear()
    yield
    _handlers.clear()


# ── Minimal concrete event ────────────────────────────────────────

@dataclass
class _Ping(DomainEvent):
    payload: str = ""


# ── Single handler ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_single_handler_receives_event():
    received: list[str] = []

    async def on_ping(event: _Ping, **_):
        received.append(event.payload)

    subscribe("_Ping", on_ping)
    await publish(_Ping(payload="hello"))
    assert received == ["hello"]


# ── Multiple handlers ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_all_handlers_fire_for_same_event():
    calls: list[str] = []

    async def h1(event: _Ping, **_):
        calls.append("h1")

    async def h2(event: _Ping, **_):
        calls.append("h2")

    subscribe("_Ping", h1)
    subscribe("_Ping", h2)
    await publish(_Ping())
    assert sorted(calls) == ["h1", "h2"]


# ── Kwargs forwarding ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_extra_kwargs_are_forwarded_to_handler():
    received: list[dict] = []

    async def on_ping(event: _Ping, db=None, **_):
        received.append({"db": db})

    subscribe("_Ping", on_ping)
    await publish(_Ping(), db="mock-session")
    assert received == [{"db": "mock-session"}]


# ── No-subscriber case ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_publish_with_no_subscribers_does_not_raise():
    await publish(_Ping(payload="orphan"))  # no subscriber registered


# ── Error isolation ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_failing_handler_does_not_block_subsequent_handlers():
    calls: list[str] = []

    async def bad_handler(event: _Ping, **_):
        raise RuntimeError("simulated failure")

    async def good_handler(event: _Ping, **_):
        calls.append("good")

    subscribe("_Ping", bad_handler)
    subscribe("_Ping", good_handler)

    await publish(_Ping())  # must not propagate the RuntimeError
    assert calls == ["good"]


# ── Isolation between event types ────────────────────────────────


@dataclass
class _Pong(DomainEvent):
    value: int = 0


@pytest.mark.asyncio
async def test_handler_only_receives_its_own_event_type():
    ping_calls: list[str] = []
    pong_calls: list[int] = []

    async def on_ping(event: _Ping, **_):
        ping_calls.append(event.payload)

    async def on_pong(event: _Pong, **_):
        pong_calls.append(event.value)

    subscribe("_Ping", on_ping)
    subscribe("_Pong", on_pong)

    await publish(_Ping(payload="ping-only"))
    assert ping_calls == ["ping-only"]
    assert pong_calls == []
