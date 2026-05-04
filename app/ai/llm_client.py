"""
Thin async wrapper around the Google Gemini Generative AI API.

Consumed by :class:`AcademicAdvisoryAgent` (and any future agent that
wants short-form narrative generation). Designed so the rest of the
codebase never imports ``google.genai`` directly:

  - ``LLMClient`` is constructible with an explicit ``genai.Client``
    instance, which keeps tests deterministic (inject a fake) and lets
    the production wiring share a single client across requests.
  - When no API key is configured, :func:`build_default_llm_client`
    returns ``None`` so callers can short-circuit without try/except.
  - Any Gemini SDK error or wall-clock timeout is caught and turned
    into ``None`` from :meth:`LLMClient.narrate_advisory`. The agent
    layer falls back to its rule-based explanation, so the LLM is
    strictly additive.

Performance notes:
  - Model defaults to ``gemini-2.0-flash`` (free-tier eligible: 15
    RPM / 1500 RPD; ample for a demo).
  - Wall-clock cap enforced via ``asyncio.wait_for`` rather than the
    SDK's transport options — keeps the timeout contract identical
    regardless of which transport google-genai is currently using.
  - The system prompt is a static module constant, which gives Gemini
    its best opportunity to apply implicit prompt caching server-side.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("app.ai.llm_client")

try:  # pragma: no cover - import guard
    from google import genai
    from google.genai import errors as genai_errors
    from google.genai import types as genai_types
except ImportError:  # pragma: no cover - SDK is a hard dep, but keep
    # the module importable in environments that haven't installed it
    # yet (e.g. lint pass before `pip install -e .`).
    genai = None  # type: ignore[assignment]
    genai_errors = None  # type: ignore[assignment]
    genai_types = None  # type: ignore[assignment]


# System prompt is a static module constant so Gemini's server-side
# prompt cache (when active) can hit on it across calls.
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
    Async narrative generator backed by the Gemini Generative AI API.

    Construct via :func:`build_default_llm_client` for production wiring,
    or pass an explicit ``client`` in tests:

        fake = FakeGenAIClient(...)
        llm = LLMClient(client=fake)
    """

    def __init__(
        self,
        *,
        client: Any,
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
        or timeout so the caller can fall back to its rule-based
        explanation.
        """
        if genai is None:  # pragma: no cover - import guard
            return None

        user_payload = json.dumps(
            {"student": student_context, "advice": structured_advice},
            default=str,
        )
        config = genai_types.GenerateContentConfig(
            system_instruction=_ADVISORY_SYSTEM_PROMPT,
            max_output_tokens=self._max_tokens,
            temperature=0.7,
        )
        try:
            response = await asyncio.wait_for(
                self._client.aio.models.generate_content(
                    model=self._model,
                    contents=user_payload,
                    config=config,
                ),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "advisory_llm_timeout",
                extra={
                    "agent_layer": "advisory",
                    "timeout_seconds": self._timeout,
                },
            )
            return None
        except genai_errors.APIError as exc:
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
    """
    Pull text out of a GenerateContentResponse. ``response.text`` is
    a convenience accessor in google-genai that flattens all text
    parts; falls back to walking candidates if absent.
    """
    text = getattr(response, "text", None)
    if text:
        return text.strip() or None

    candidates = getattr(response, "candidates", None) or []
    parts: list[str] = []
    for cand in candidates:
        content = getattr(cand, "content", None)
        for part in getattr(content, "parts", None) or []:
            t = getattr(part, "text", None)
            if t:
                parts.append(t)
    joined = "".join(parts).strip()
    return joined or None


def build_default_llm_client() -> Optional[LLMClient]:
    """
    Construct the production :class:`LLMClient` from settings, or
    ``None`` when ``GEMINI_API_KEY`` is unset. Returning ``None``
    is the documented "LLM disabled" signal — agents must accept it
    and degrade to rule-based output.
    """
    if not settings.GEMINI_API_KEY:
        return None
    if genai is None:  # pragma: no cover - import guard
        return None
    raw_client = genai.Client(api_key=settings.GEMINI_API_KEY)
    return LLMClient(client=raw_client)
