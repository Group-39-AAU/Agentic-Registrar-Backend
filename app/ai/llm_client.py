"""
Thin async wrapper around the Anthropic Messages API.

Consumed by :class:`AcademicAdvisoryAgent` (and any future agent that
wants short-form narrative generation). Designed so the rest of the
codebase never imports the ``anthropic`` package directly:

  - ``LLMClient`` is constructible with an explicit ``AsyncAnthropic``
    instance, which keeps tests deterministic (inject a fake) and lets
    the production wiring share a single client across requests.
  - When no API key is configured, :func:`build_default_llm_client`
    returns ``None`` so callers can short-circuit without try/except.
  - Any Anthropic SDK error (rate limit, timeout, 5xx, connection
    failure) is caught and turned into ``None`` from
    :meth:`LLMClient.narrate_advisory`. The agent layer falls back to
    its rule-based explanation, so the LLM is strictly additive.

Performance notes:
  - Model defaults to ``claude-haiku-4-5`` (cheap + fast; 200K context
    is far more than the advisory prompt needs).
  - The system prompt is marked with ``cache_control={"type":
    "ephemeral"}`` so repeated advisory calls within the 5-minute TTL
    only pay the prefix cost once.
  - ``with_options(timeout=...)`` enforces a hard wall-clock cap; the
    default 5s is comfortable for Haiku and short enough that a stuck
    request never blocks a user-facing /advisory/evaluate response.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("app.ai.llm_client")

try:  # pragma: no cover - import guard
    import anthropic
    from anthropic import AsyncAnthropic
except ImportError:  # pragma: no cover - SDK is a hard dep, but keep
    # the module importable in environments that haven't installed it
    # yet (e.g. lint pass before `pip install -e .`).
    anthropic = None  # type: ignore[assignment]
    AsyncAnthropic = None  # type: ignore[assignment,misc]


# System prompt is intentionally stable across calls so the prompt
# cache hit rate stays high. Anything per-request goes in the user
# message, not here.
_ADVISORY_SYSTEM_PROMPT = (
    "You are an academic advisor at Addis Ababa University writing "
    "short, plain-English guidance for an undergraduate registrar "
    "portal. You will receive a JSON object describing the student's "
    "current CGPA, proposed credit load, risk level, gap analysis, "
    "and the courses our rule engine recommends.\n\n"
    "Write a single paragraph (3-5 sentences, max ~120 words) that:\n"
    "  1. Acknowledges the student's standing and proposed load.\n"
    "  2. Explains why the recommended next courses make sense.\n"
    "  3. If risk is HIGH or MEDIUM, names the concrete trade-off "
    "and the action they should take (lighter load, advisor visit, "
    "core course first).\n"
    "  4. Stays factual: do not invent course codes or grades that "
    "are not in the input. Do not promise registration outcomes.\n\n"
    "Tone: warm, direct, second person. No bullet lists, no headings, "
    "no markdown."
)


class LLMClient:
    """
    Async narrative generator backed by the Anthropic Messages API.

    Construct via :func:`build_default_llm_client` for production wiring,
    or pass an explicit ``client`` in tests:

        fake = FakeAnthropic(...)
        llm = LLMClient(client=fake)
    """

    def __init__(
        self,
        *,
        client: "AsyncAnthropic",
        model: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> None:
        self._client = client
        self._model = model or settings.ADVISORY_LLM_MODEL
        self._timeout = (
            timeout_seconds if timeout_seconds is not None
            else settings.ADVISORY_LLM_TIMEOUT_SECONDS
        )
        self._max_tokens = max_tokens or settings.ADVISORY_LLM_MAX_TOKENS

    async def narrate_advisory(
        self,
        structured_advice: dict[str, Any],
        student_context: dict[str, Any],
    ) -> Optional[str]:
        """
        Turn the agent's structured advisory verdict into a short
        student-facing paragraph. Returns ``None`` on any SDK failure
        so the caller can fall back to its rule-based explanation.
        """
        if anthropic is None:  # pragma: no cover - import guard
            return None

        user_payload = {
            "student": student_context,
            "advice": structured_advice,
        }
        try:
            scoped = self._client.with_options(timeout=self._timeout)
            response = await scoped.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=[
                    {
                        "type": "text",
                        "text": _ADVISORY_SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[
                    {
                        "role": "user",
                        "content": json.dumps(user_payload, default=str),
                    }
                ],
            )
        except anthropic.APIError as exc:
            logger.warning(
                "advisory_llm_failed",
                extra={
                    "agent_layer": "advisory",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            return None
        except Exception as exc:  # network / json / unexpected
            logger.warning(
                "advisory_llm_unexpected_error",
                extra={
                    "agent_layer": "advisory",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            return None

        return _extract_text(response)


def _extract_text(response: Any) -> Optional[str]:
    """Pull the first text block out of a Messages API response."""
    blocks = getattr(response, "content", None) or []
    parts: list[str] = []
    for block in blocks:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    joined = "".join(parts).strip()
    return joined or None


def build_default_llm_client() -> Optional[LLMClient]:
    """
    Construct the production :class:`LLMClient` from settings, or
    ``None`` when ``ANTHROPIC_API_KEY`` is unset. Returning ``None``
    is the documented "LLM disabled" signal — agents must accept it
    and degrade to rule-based output.
    """
    if not settings.ANTHROPIC_API_KEY:
        return None
    if AsyncAnthropic is None:  # pragma: no cover - import guard
        return None
    raw_client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return LLMClient(client=raw_client)
