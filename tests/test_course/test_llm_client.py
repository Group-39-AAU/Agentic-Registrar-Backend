"""
Unit tests for app.ai.llm_client.LLMClient.

The Anthropic SDK is never called for real here — every test injects
a fake async client that records the request and returns a canned
response (or raises). The LLMClient must:

  - Forward the configured model + max_tokens.
  - Mark the system prompt for ephemeral prompt-cache reuse.
  - Apply the per-request hard timeout via ``with_options``.
  - Return ``None`` (graceful fallback) on every Anthropic SDK error
    type and on unexpected exceptions.
  - Strip and concatenate text content blocks from the response.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import anthropic
import pytest

from app.ai.llm_client import LLMClient, build_default_llm_client


# ── Fakes ────────────────────────────────────────────────────────


def _make_response(text: str) -> Any:
    """Mimic an anthropic Message with a single text content block."""
    block = MagicMock()
    block.text = text
    response = MagicMock()
    response.content = [block]
    return response


class FakeAnthropic:
    """
    Minimal stand-in for ``AsyncAnthropic``.

    Captures the kwargs passed to ``messages.create`` so tests can
    assert on the wire-level shape, and lets each test choose what
    ``create`` returns or raises.
    """

    def __init__(
        self,
        *,
        return_value: Any = None,
        raise_exc: BaseException | None = None,
    ):
        self.return_value = return_value
        self.raise_exc = raise_exc
        self.last_create_kwargs: dict[str, Any] | None = None
        self.with_options_calls: list[dict[str, Any]] = []

        async def _create(**kwargs):
            self.last_create_kwargs = kwargs
            if self.raise_exc is not None:
                raise self.raise_exc
            return self.return_value

        self.messages = MagicMock()
        self.messages.create = AsyncMock(side_effect=_create)

    def with_options(self, **kwargs):
        self.with_options_calls.append(kwargs)
        return self


# ── Helpers ──────────────────────────────────────────────────────


def _make_llm(fake: FakeAnthropic, **overrides: Any) -> LLMClient:
    return LLMClient(
        client=fake,  # type: ignore[arg-type]
        model=overrides.get("model", "claude-haiku-4-5"),
        timeout_seconds=overrides.get("timeout_seconds", 5.0),
        max_tokens=overrides.get("max_tokens", 600),
    )


_ADVICE = {
    "risk_status": "MEDIUM",
    "recommended_courses": [
        {"code": "CS201", "title": "Data Structures"},
    ],
    "gap_analysis": {"remaining_count": 3, "curriculum_size": 8},
    "explanation": "Rule-based fallback line.",
    "requires_officer_review": False,
}
_STUDENT = {
    "student_id": "UGR/0001/14",
    "department": "Computer Science",
    "current_semester": 2,
    "cgpa": 3.1,
    "proposed_credits": 16,
}


# ── Happy path ───────────────────────────────────────────────────


async def test_narrate_advisory_returns_text_on_success():
    fake = FakeAnthropic(return_value=_make_response(
        "You're holding a solid 3.1 CGPA — a 16-credit load is reasonable."
    ))
    llm = _make_llm(fake)

    text = await llm.narrate_advisory(_ADVICE, _STUDENT)

    assert text is not None
    assert "3.1" in text
    fake.messages.create.assert_awaited_once()


async def test_request_uses_configured_model_and_token_cap():
    fake = FakeAnthropic(return_value=_make_response("ok"))
    llm = _make_llm(fake, model="claude-haiku-4-5", max_tokens=400)

    await llm.narrate_advisory(_ADVICE, _STUDENT)

    kwargs = fake.last_create_kwargs
    assert kwargs is not None
    assert kwargs["model"] == "claude-haiku-4-5"
    assert kwargs["max_tokens"] == 400


async def test_system_prompt_is_marked_for_ephemeral_cache():
    """SDS-aligned cost optimisation: stable system prompt must be cached."""
    fake = FakeAnthropic(return_value=_make_response("ok"))
    llm = _make_llm(fake)

    await llm.narrate_advisory(_ADVICE, _STUDENT)

    system_blocks = fake.last_create_kwargs["system"]
    assert isinstance(system_blocks, list) and len(system_blocks) == 1
    assert system_blocks[0]["type"] == "text"
    assert system_blocks[0]["cache_control"] == {"type": "ephemeral"}


async def test_user_message_carries_serialised_payload():
    fake = FakeAnthropic(return_value=_make_response("ok"))
    llm = _make_llm(fake)

    await llm.narrate_advisory(_ADVICE, _STUDENT)

    messages = fake.last_create_kwargs["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    payload = messages[0]["content"]
    # JSON-encoded so model receives a clean structured input
    assert "UGR/0001/14" in payload
    assert "MEDIUM" in payload


async def test_hard_timeout_is_applied_via_with_options():
    fake = FakeAnthropic(return_value=_make_response("ok"))
    llm = _make_llm(fake, timeout_seconds=2.5)

    await llm.narrate_advisory(_ADVICE, _STUDENT)

    assert fake.with_options_calls == [{"timeout": 2.5}]


# ── Failure modes — every one of these must yield None ──────────


@pytest.mark.parametrize(
    "exc",
    [
        anthropic.APITimeoutError(request=MagicMock()),
        anthropic.APIConnectionError(request=MagicMock()),
        # APIStatusError needs a response object; build one with the
        # fields the SDK reads (status_code, headers).
        anthropic.APIStatusError(
            "boom",
            response=MagicMock(status_code=500, headers={}),
            body=None,
        ),
        RuntimeError("totally unexpected"),
    ],
)
async def test_narrate_advisory_returns_none_on_sdk_errors(exc):
    fake = FakeAnthropic(raise_exc=exc)
    llm = _make_llm(fake)

    text = await llm.narrate_advisory(_ADVICE, _STUDENT)

    assert text is None


async def test_narrate_advisory_returns_none_on_empty_content():
    """An empty/whitespace response is treated as a fallback signal."""
    empty = MagicMock()
    empty.content = []
    fake = FakeAnthropic(return_value=empty)
    llm = _make_llm(fake)

    assert await llm.narrate_advisory(_ADVICE, _STUDENT) is None


async def test_narrate_advisory_concatenates_multiple_text_blocks():
    block_a = MagicMock(); block_a.text = "Part one. "
    block_b = MagicMock(); block_b.text = "Part two."
    response = MagicMock()
    response.content = [block_a, block_b]
    fake = FakeAnthropic(return_value=response)
    llm = _make_llm(fake)

    text = await llm.narrate_advisory(_ADVICE, _STUDENT)

    assert text == "Part one. Part two."


# ── build_default_llm_client ──────────────────────────────────────


def test_build_default_llm_client_returns_none_without_api_key(monkeypatch):
    from app.ai import llm_client as mod
    monkeypatch.setattr(mod.settings, "ANTHROPIC_API_KEY", "")
    assert build_default_llm_client() is None


def test_build_default_llm_client_constructs_client_with_api_key(monkeypatch):
    from app.ai import llm_client as mod
    monkeypatch.setattr(mod.settings, "ANTHROPIC_API_KEY", "sk-test")

    captured: dict[str, Any] = {}

    class _StubAsync:
        def __init__(self, *, api_key: str):
            captured["api_key"] = api_key

    monkeypatch.setattr(mod, "AsyncAnthropic", _StubAsync)

    client = build_default_llm_client()
    assert isinstance(client, LLMClient)
    assert captured["api_key"] == "sk-test"
