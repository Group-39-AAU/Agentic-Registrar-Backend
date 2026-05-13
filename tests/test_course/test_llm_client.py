"""
Unit tests for app.ai.llm_client.LLMClient.

The Gemini SDK is never called for real here — every test injects a
fake async client that records the request and returns a canned
response (or raises). The LLMClient must:

  - Forward the configured model + max_output_tokens.
  - Pass the system prompt as ``GenerateContentConfig.system_instruction``
    so Gemini's server-side prompt cache has its best chance.
  - Apply the per-request hard timeout (asyncio.wait_for).
  - Return ``None`` (graceful fallback) on every Gemini SDK error
    type, on timeout, and on unexpected exceptions.
  - Strip and concatenate text from the response.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from google.genai import errors as genai_errors

from app.ai.llm_client import LLMClient, build_default_llm_client


# ── Fakes ────────────────────────────────────────────────────────


def _make_response(text: str) -> Any:
    """Mimic a GenerateContentResponse with a top-level .text accessor."""
    response = MagicMock()
    response.text = text
    return response


class FakeGenAIClient:
    """
    Minimal stand-in for ``google.genai.Client``.

    Captures the kwargs passed to ``aio.models.generate_content`` so
    tests can assert on the wire-level shape, and lets each test
    choose what ``generate_content`` returns or raises.
    """

    def __init__(
        self,
        *,
        return_value: Any = None,
        raise_exc: BaseException | None = None,
        delay_seconds: float = 0.0,
    ):
        self.return_value = return_value
        self.raise_exc = raise_exc
        self.delay_seconds = delay_seconds
        self.last_kwargs: dict[str, Any] | None = None

        async def _generate(**kwargs):
            self.last_kwargs = kwargs
            if self.delay_seconds:
                await asyncio.sleep(self.delay_seconds)
            if self.raise_exc is not None:
                raise self.raise_exc
            return self.return_value

        models = MagicMock()
        models.generate_content = AsyncMock(side_effect=_generate)
        aio = MagicMock()
        aio.models = models
        self.aio = aio


# ── Helpers ──────────────────────────────────────────────────────


def _make_llm(fake: FakeGenAIClient, **overrides: Any) -> LLMClient:
    return LLMClient(
        client=fake,
        model=overrides.get("model", "gemini-2.0-flash"),
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
    fake = FakeGenAIClient(return_value=_make_response(
        "You're holding a solid 3.1 CGPA — a 16-credit load is reasonable."
    ))
    llm = _make_llm(fake)

    text = await llm.narrate_advisory(_ADVICE, _STUDENT)

    assert text is not None
    assert "3.1" in text
    fake.aio.models.generate_content.assert_awaited_once()


async def test_request_uses_configured_model_and_token_cap():
    fake = FakeGenAIClient(return_value=_make_response("ok"))
    llm = _make_llm(fake, model="gemini-2.0-flash", max_tokens=400)

    await llm.narrate_advisory(_ADVICE, _STUDENT)

    kwargs = fake.last_kwargs
    assert kwargs is not None
    assert kwargs["model"] == "gemini-2.0-flash"
    assert kwargs["config"].max_output_tokens == 400


async def test_system_prompt_is_passed_via_system_instruction():
    """Lets Gemini cache the static system prompt server-side."""
    fake = FakeGenAIClient(return_value=_make_response("ok"))
    llm = _make_llm(fake)

    await llm.narrate_advisory(_ADVICE, _STUDENT)

    config = fake.last_kwargs["config"]
    assert config.system_instruction
    assert "Addis Ababa University" in config.system_instruction


async def test_user_payload_carries_serialised_inputs():
    fake = FakeGenAIClient(return_value=_make_response("ok"))
    llm = _make_llm(fake)

    await llm.narrate_advisory(_ADVICE, _STUDENT)

    contents = fake.last_kwargs["contents"]
    # Single JSON-encoded string keeps the prompt boundary clean
    assert isinstance(contents, str)
    assert "UGR/0001/14" in contents
    assert "MEDIUM" in contents


async def test_hard_timeout_is_enforced():
    """A slow LLM must not block the caller past the configured cap."""
    fake = FakeGenAIClient(
        return_value=_make_response("late"), delay_seconds=2.0,
    )
    llm = _make_llm(fake, timeout_seconds=0.05)

    text = await llm.narrate_advisory(_ADVICE, _STUDENT)

    assert text is None  # timeout → fallback


# ── Failure modes — every one of these must yield None ──────────


@pytest.mark.parametrize(
    "exc",
    [
        genai_errors.ClientError(
            code=400,
            response_json={"error": {"message": "bad input"}},
        ),
        genai_errors.ServerError(
            code=500,
            response_json={"error": {"message": "boom"}},
        ),
        RuntimeError("totally unexpected"),
    ],
)
async def test_narrate_advisory_returns_none_on_sdk_errors(exc):
    fake = FakeGenAIClient(raise_exc=exc)
    llm = _make_llm(fake)

    text = await llm.narrate_advisory(_ADVICE, _STUDENT)

    assert text is None


async def test_narrate_advisory_returns_none_on_empty_text():
    """An empty/whitespace response is treated as a fallback signal."""
    empty = MagicMock()
    empty.text = ""
    empty.candidates = []
    fake = FakeGenAIClient(return_value=empty)
    llm = _make_llm(fake)

    assert await llm.narrate_advisory(_ADVICE, _STUDENT) is None


async def test_narrate_advisory_falls_back_to_candidates_when_text_missing():
    """Some response shapes only expose text via candidates[].content.parts[]."""
    response = MagicMock()
    response.text = None
    part_a = MagicMock(); part_a.text = "Part one. "
    part_b = MagicMock(); part_b.text = "Part two."
    content = MagicMock(); content.parts = [part_a, part_b]
    candidate = MagicMock(); candidate.content = content
    response.candidates = [candidate]
    fake = FakeGenAIClient(return_value=response)
    llm = _make_llm(fake)

    text = await llm.narrate_advisory(_ADVICE, _STUDENT)

    assert text == "Part one. Part two."


# ── build_default_llm_client ──────────────────────────────────────


def test_build_default_llm_client_returns_none_without_api_key(monkeypatch):
    from app.ai import llm_client as mod
    monkeypatch.setattr(mod.settings, "GEMINI_API_KEY", "")
    assert build_default_llm_client() is None


def test_build_default_llm_client_constructs_client_with_api_key(monkeypatch):
    from app.ai import llm_client as mod
    monkeypatch.setattr(mod.settings, "GEMINI_API_KEY", "AIza-test")

    captured: dict[str, Any] = {}

    class _StubGenaiNamespace:
        @staticmethod
        def Client(*, api_key: str):
            captured["api_key"] = api_key
            return MagicMock()

    monkeypatch.setattr(mod, "genai", _StubGenaiNamespace)

    client = build_default_llm_client()
    assert isinstance(client, LLMClient)
    assert captured["api_key"] == "AIza-test"
