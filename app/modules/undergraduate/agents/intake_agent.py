"""
Intake Validation Agent — LangGraph StateGraph.

The first AI agent in the Agentic Registrar pipeline. It validates that
an application is complete before it can proceed to document verification.

Checks performed:
    1. Profile completeness (sponsorship, stream, program choices)
    2. Payment verification (payment_status = COMPLETED)

If all checks pass → transitions to UNDER_VERIFICATION.
If any fail → writes AIEvaluation with FLAG_FOR_REVIEW and keeps current status.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any

from langgraph.graph import END, StateGraph

from app.core.logging import get_logger, write_audit_log
from app.shared.enums import (
    ApplicationStatus,
    DecisionType,
    DocumentType,
    PaymentStatus,
    SponsorshipType,
    UserRole,
)

logger = get_logger("ai.intake_agent")

AGENT_VERSION = "intake-agent-v1.0"

# ── Agent State ──────────────────────────────────────────────

@dataclass
class IntakeState:
    """Mutable state passed through the LangGraph nodes."""
    application_id: uuid.UUID = field(default_factory=uuid.uuid4)
    sponsorship_type: str = ""
    stream: str = ""
    admission_number: str = ""
    program_choice_1_id: Any = None
    program_choice_2_id: Any = None
    program_choice_3_id: Any = None
    payment_status: str = ""
    current_status: str = ""

    # Populated by agent nodes
    checks_passed: list[str] = field(default_factory=list)
    checks_failed: list[str] = field(default_factory=list)
    traces: list[dict] = field(default_factory=list)
    overall_result: str = ""  # "PASS" or "FLAG_FOR_REVIEW"


# ── Agent Nodes ──────────────────────────────────────────────

def check_profile_complete(state: IntakeState) -> IntakeState:
    """Node 1: Verify all required form fields are present."""
    reasoning_lines = []
    passed = True

    # Sponsorship type
    if not state.sponsorship_type:
        reasoning_lines.append("FAIL: sponsorship_type is missing")
        passed = False
    else:
        reasoning_lines.append(f"OK: sponsorship_type = {state.sponsorship_type}")

    # Stream
    if not state.stream:
        reasoning_lines.append("FAIL: stream is missing")
        passed = False
    else:
        reasoning_lines.append(f"OK: stream = {state.stream}")

    # Program choices (self-sponsored only)
    if state.sponsorship_type == SponsorshipType.SELF_SPONSORED.value:
        choices = [state.program_choice_1_id, state.program_choice_2_id, state.program_choice_3_id]
        if any(c is None for c in choices):
            reasoning_lines.append("FAIL: self-sponsored but missing program choices")
            passed = False
        else:
            reasoning_lines.append("OK: all 3 program choices provided")
    else:
        reasoning_lines.append("OK: government-sponsored — program choices not required")

    # Admission number
    if not state.admission_number:
        reasoning_lines.append("FAIL: admission_number is missing")
        passed = False
    else:
        reasoning_lines.append(f"OK: admission_number = {state.admission_number}")

    result = "PASS" if passed else "FAIL"
    if passed:
        state.checks_passed.append("profile_completeness")
    else:
        state.checks_failed.append("profile_completeness")

    state.traces.append({
        "step_name": "check_profile_complete",
        "reasoning_log": "\n".join(reasoning_lines),
        "result": result,
    })

    logger.info("Profile check: %s for application %s", result, state.application_id)
    return state


def check_payment_verified(state: IntakeState) -> IntakeState:
    """Node 2: Confirm payment has been completed."""
    reasoning_lines = []

    if state.payment_status == PaymentStatus.COMPLETED.value:
        reasoning_lines.append("OK: payment_status = COMPLETED")
        state.checks_passed.append("payment_verified")
        result = "PASS"
    else:
        reasoning_lines.append(f"FAIL: payment_status = {state.payment_status} (expected COMPLETED)")
        state.checks_failed.append("payment_verified")
        result = "FAIL"

    state.traces.append({
        "step_name": "check_payment_verified",
        "reasoning_log": "\n".join(reasoning_lines),
        "result": result,
    })

    logger.info("Payment check: %s for application %s", result, state.application_id)
    return state


def decide(state: IntakeState) -> IntakeState:
    """Node 3: Final decision — pass or flag for review."""
    if not state.checks_failed:
        state.overall_result = "PASS"
        reasoning = (
            "All intake checks passed. Application is complete and ready "
            "for document verification.\n"
            f"Checks passed: {state.checks_passed}"
        )
    else:
        state.overall_result = "FLAG_FOR_REVIEW"
        reasoning = (
            "Application is INCOMPLETE. The following checks failed:\n"
            f"  {state.checks_failed}\n"
            f"Checks passed: {state.checks_passed}\n"
            "Application remains in current status until issues are resolved."
        )

    state.traces.append({
        "step_name": "decide",
        "reasoning_log": reasoning,
        "result": state.overall_result,
    })

    logger.info(
        "Intake decision: %s for application %s (passed=%d, failed=%d)",
        state.overall_result, state.application_id,
        len(state.checks_passed), len(state.checks_failed),
    )
    return state


# ── Build the Graph ──────────────────────────────────────────

def build_intake_graph() -> StateGraph:
    """
    Construct the LangGraph StateGraph for the Intake Validation Agent.

    Flow: check_profile → check_payment → decide → END
    """
    graph = StateGraph(IntakeState)

    graph.add_node("check_profile_complete", check_profile_complete)
    graph.add_node("check_payment_verified", check_payment_verified)
    graph.add_node("decide", decide)

    graph.set_entry_point("check_profile_complete")
    graph.add_edge("check_profile_complete", "check_payment_verified")
    graph.add_edge("check_payment_verified", "decide")
    graph.add_edge("decide", END)

    return graph.compile()


# ── Public API ──────────────────────────────────────────────

async def run_intake_validation(
    application_id: uuid.UUID,
    sponsorship_type: str,
    stream: str,
    admission_number: str,
    program_choice_1_id: Any,
    program_choice_2_id: Any,
    program_choice_3_id: Any,
    payment_status: str,
    current_status: str,
) -> IntakeState:
    """
    Run the full intake validation pipeline. Returns the final IntakeState
    with checks_passed, checks_failed, traces, and overall_result.
    """
    initial_state = IntakeState(
        application_id=application_id,
        sponsorship_type=sponsorship_type,
        stream=stream,
        admission_number=admission_number,
        program_choice_1_id=program_choice_1_id,
        program_choice_2_id=program_choice_2_id,
        program_choice_3_id=program_choice_3_id,
        payment_status=payment_status,
        current_status=current_status,
    )

    compiled_graph = build_intake_graph()
    final_state = compiled_graph.invoke(initial_state)

    return final_state
