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
  - Model defaults to ``gemini-2.5-flash-lite`` (free-tier eligible: 15
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


class LLMUnavailableError(RuntimeError):
    """
    Raised by :meth:`LLMClient.consult_academic_plan` when the Gemini
    call cannot be completed (no API key, SDK import failure, timeout,
    or any API error). The advisory consult endpoints catch this and
    surface a 503 to the student per the documented hard-fail contract
    — there is no rule-based fallback for the demand-driven consult
    flow because the LLM *is* the reasoning engine in that path.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

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


# Static system prompt for the demand-driven consult flow. Held as a
# module constant so Gemini's implicit cache (when active) can reuse
# it across calls — only the per-student JSON payload changes.
_CONSULTATION_SYSTEM_PROMPT = (
    "You are the senior academic advisor at Addis Ababa University. "
    "Your job is to keep undergraduate students on track for on-time "
    "graduation. A student is asking for guidance through their "
    "registrar portal; you will receive a structured JSON object "
    "describing:\n"
    "  - student     : id, current semester, CGPA, sponsorship type\n"
    "  - department  : name and the per-semester required-course\n"
    "                  sequence with credit hours and prerequisites\n"
    "  - history     : courses the student has already passed\n"
    "  - current_term: open registration term (name + dates)\n"
    "  - draft       : courses the student already has on the\n"
    "                  current registration draft (for plan and\n"
    "                  add-drop modes)\n"
    "  - mode        : PRE_REGISTRATION | REGISTRATION_PLAN | ADD_DROP\n"
    "  - proposed_changes (ADD_DROP only): courses the student is\n"
    "                  considering adding / dropping. May be EMPTY —\n"
    "                  see the ADD_DROP modes below.\n\n"
    "ADD_DROP modes:\n"
    "  * GUIDED  — proposed_changes has at least one add or drop\n"
    "    code. Evaluate that specific hypothetical: will it keep the\n"
    "    student on track, what is the graduation impact, are the\n"
    "    prerequisites for any add satisfied, does the resulting\n"
    "    load stay inside 12-22 ECTS, are any of the drops on the\n"
    "    critical path?\n"
    "  * PROACTIVE — proposed_changes is EMPTY (both add and drop\n"
    "    lists). The student is asking 'what should I change?'.\n"
    "    Analyse the active registration against the curriculum +\n"
    "    history and either (a) recommend specific courses to add\n"
    "    and/or drop (with reasons rooted in graduation pacing,\n"
    "    prereq alignment, or load), or (b) confirm the plan is\n"
    "    healthy and no change is needed. Use the warnings array\n"
    "    for the 'do this, then do that' explanations; populate\n"
    "    recommended_courses with the courses you propose adding.\n\n"
    "Reason carefully through the prerequisite graph. The university "
    "enforces a 12 ECTS minimum and 22 ECTS maximum credit load per "
    "term. Lecture days are MON-FRI only. Your goal is to help the "
    "student finish all required courses in the standard semester "
    "count for their program — flag any choice that would force them "
    "to take a course out-of-sequence and lose a semester or a year.\n\n"
    "RULES:\n"
    "  1. Never recommend a course the student has already passed.\n"
    "  2. Never recommend a course whose prerequisites are unmet "
    "(unless you explicitly flag it as REQUIRES_OVERRIDE so the "
    "Department Head can grant a one-time bypass).\n"
    "  3. Prioritise mandatory core courses for the student's\n"
    "     current semester before electives or look-ahead courses.\n"
    "  4. Respect the 12-22 ECTS load window when sizing the plan.\n"
    "  5. Be concrete: cite course codes from the input; never "
    "invent codes, titles, prerequisites, or grades.\n"
    "  6. If you cannot give a confident answer (input contradicts "
    "itself, missing curriculum data), set `verdict` to NEEDS_REVIEW "
    "and explain why in `narrative`.\n\n"
    "OUTPUT: respond with one JSON object that conforms to the "
    "response schema you have been given. Do not add prose outside "
    "the JSON. Keep the narrative warm, second-person, 3-6 sentences."
)


# Response schema for ``consult_academic_plan``. Modelled as a plain
# dict (Gemini's ``response_schema`` parameter accepts an OpenAPI-3
# style schema) so we don't pull in any framework-specific types.
_CONSULTATION_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["ON_TRACK", "AT_RISK", "OFF_TRACK", "NEEDS_REVIEW"],
            "description": (
                "Headline assessment. ON_TRACK = the proposed plan "
                "(or, in PRE_REGISTRATION mode, the recommended plan) "
                "keeps the student on the standard graduation arc. "
                "AT_RISK = the plan is technically valid but adds "
                "delay risk. OFF_TRACK = the plan will demonstrably "
                "delay graduation by at least one semester. "
                "NEEDS_REVIEW = the agent cannot give a confident "
                "answer and the student should see a human advisor."
            ),
        },
        "risk_status": {
            "type": "string",
            "enum": ["LOW", "MEDIUM", "HIGH"],
            "description": (
                "Same scale as the rule engine's RiskStatus so the "
                "service layer can route HIGH-risk consultations into "
                "the existing officer review queue if it chooses to."
            ),
        },
        "recommended_courses": {
            "type": "array",
            "description": (
                "Concrete next-step courses the student should take "
                "this term. Order matters: most important first."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "course_code": {"type": "string"},
                    "title": {"type": "string"},
                    "credit_hours": {"type": "integer"},
                    "is_core": {"type": "boolean"},
                    "reason": {"type": "string"},
                    "requires_override": {"type": "boolean"},
                },
                "required": [
                    "course_code", "title", "credit_hours",
                    "is_core", "reason",
                ],
            },
        },
        "warnings": {
            "type": "array",
            "description": (
                "Specific concerns about the proposed plan or the "
                "current academic situation. Each warning should be "
                "actionable (what the student can do about it)."
            ),
            "items": {"type": "string"},
        },
        "graduation_impact": {
            "type": "object",
            "description": (
                "Forward-looking estimate of when the student will "
                "graduate if they follow this plan."
            ),
            "properties": {
                "semesters_remaining": {"type": "integer"},
                "on_track": {"type": "boolean"},
                "expected_graduation_semester": {"type": "integer"},
                "delay_semesters": {"type": "integer"},
                "critical_path_courses": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Course codes whose timely completion most "
                        "constrains graduation date — the courses "
                        "the student must not slip on."
                    ),
                },
            },
            "required": [
                "semesters_remaining", "on_track",
                "expected_graduation_semester", "delay_semesters",
            ],
        },
        "narrative": {
            "type": "string",
            "description": (
                "Plain-English summary the student will read. "
                "3-6 sentences, second person, warm and direct."
            ),
        },
    },
    "required": [
        "verdict", "risk_status", "recommended_courses",
        "warnings", "graduation_impact", "narrative",
    ],
}


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
        print("\n=== ADVISORY LLM CALL ===")
        print(f"System Prompt:\n{_ADVISORY_SYSTEM_PROMPT}")
        print(f"\nUser Context/Payload:\n{user_payload}")
        print("=== END ADVISORY LLM CALL ===\n")
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


    async def consult_academic_plan(
        self,
        consultation_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Reason over the full student + curriculum context and return
        a structured advisory verdict.

        Hard-fail contract: any failure (missing API key, SDK
        unavailable, timeout, API error, malformed JSON) raises
        :class:`LLMUnavailableError`. The advisory consult endpoints
        translate that to a 503 response so the student knows the
        deep analysis did not run, rather than receiving silent
        rule-based output.

        ``consultation_payload`` is the full context the agent built
        (student profile, curriculum, history, draft, mode, proposed
        changes). The system prompt + response schema do the heavy
        lifting; we just serialise and dispatch.
        """
        if genai is None:
            raise LLMUnavailableError(
                "google-genai SDK is not installed in this environment."
            )

        user_payload = json.dumps(consultation_payload, default=str)
        config = genai_types.GenerateContentConfig(
            system_instruction=_CONSULTATION_SYSTEM_PROMPT,
            max_output_tokens=self._max_tokens,
            temperature=0.4,
            response_mime_type="application/json",
            response_schema=_CONSULTATION_RESPONSE_SCHEMA,
        )
        print("\n=== CONSULTATION LLM CALL ===")
        print(f"System Prompt:\n{_CONSULTATION_SYSTEM_PROMPT}")
        print(f"\nUser Context/Payload:\n{user_payload}")
        print("=== END CONSULTATION LLM CALL ===\n")
        try:
            response = await asyncio.wait_for(
                self._client.aio.models.generate_content(
                    model=self._model,
                    contents=user_payload,
                    config=config,
                ),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError as exc:
            logger.warning(
                "advisory_consult_llm_timeout",
                extra={
                    "agent_layer": "advisory_consult",
                    "timeout_seconds": self._timeout,
                },
            )
            raise LLMUnavailableError(
                f"Advisory LLM call timed out after {self._timeout}s."
            ) from exc
        except genai_errors.APIError as exc:
            logger.warning(
                "advisory_consult_llm_failed",
                extra={
                    "agent_layer": "advisory_consult",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            raise LLMUnavailableError(
                f"Gemini API error: {type(exc).__name__}: {exc}"
            ) from exc
        except Exception as exc:
            logger.warning(
                "advisory_consult_llm_unexpected_error",
                extra={
                    "agent_layer": "advisory_consult",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            raise LLMUnavailableError(
                f"Unexpected LLM error: {type(exc).__name__}: {exc}"
            ) from exc

        raw_text = _extract_text(response)
        if not raw_text:
            raise LLMUnavailableError(
                "Gemini returned an empty response body."
            )
        try:
            return json.loads(raw_text)
        except json.JSONDecodeError as exc:
            logger.warning(
                "advisory_consult_llm_invalid_json",
                extra={
                    "agent_layer": "advisory_consult",
                    "raw_excerpt": raw_text[:500],
                },
            )
            raise LLMUnavailableError(
                "Gemini response was not valid JSON despite "
                "structured-output mode."
            ) from exc


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
