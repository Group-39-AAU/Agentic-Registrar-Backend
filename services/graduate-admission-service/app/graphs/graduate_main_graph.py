from __future__ import annotations

from typing import Any

from langgraph.graph import StateGraph

from agent_core.retry import async_retry
from agent_core.state import append_history_entry, merge_state

from ..config.settings import get_settings
from ..domain.enums import ApplicationStatus
from ..infrastructure.clients import (
    get_gat_provider,
    get_payment_provider,
    get_transcript_verification_provider,
)
from ..state.models import GraduateWorkflowState


def load_application(state: GraduateWorkflowState) -> GraduateWorkflowState:
    new_history = append_history_entry(state.history, {"step": "load_application"})
    return GraduateWorkflowState(data=state.data, context=state.context, history=new_history)


def validate_fields(state: GraduateWorkflowState) -> GraduateWorkflowState:
    new_data = merge_state(state.data, {"status": ApplicationStatus.VALIDATION_PENDING})
    new_history = append_history_entry(state.history, {"step": "validate_fields"})
    return GraduateWorkflowState(data=new_data, context=state.context, history=new_history)


@async_retry()
async def verify_payment(state: GraduateWorkflowState) -> GraduateWorkflowState:
    payment = get_payment_provider()
    ok = payment.verify_payment(state.data.get("application_id", ""))
    force_fail = bool(state.data.get("force_payment_failure", False))
    if force_fail:
        ok = False
    new_data = merge_state(
        state.data,
        {"payment_verified": ok},
    )
    new_history = append_history_entry(state.history, {"step": "verify_payment"})
    return GraduateWorkflowState(data=new_data, context=state.context, history=new_history)


def decide_after_payment(state: GraduateWorkflowState) -> str:
    if not state.data.get("payment_verified", True):
        return "finalize_decision"
    return "fetch_gat_result"


@async_retry()
async def fetch_gat_result(state: GraduateWorkflowState) -> GraduateWorkflowState:
    gat = get_gat_provider()
    applicant_id = str(state.data.get("applicant_id", ""))
    score = gat.fetch_gat_score(applicant_id) or 0.0
    new_data = merge_state(state.data, {"gat_score": score, "status": ApplicationStatus.GAT_VERIFIED})
    new_history = append_history_entry(state.history, {"step": "fetch_gat_result"})
    return GraduateWorkflowState(data=new_data, context=state.context, history=new_history)


def extract_transcript_data(state: GraduateWorkflowState) -> GraduateWorkflowState:
    new_data = merge_state(state.data, {"transcript_score": 80.0})
    new_history = append_history_entry(state.history, {"step": "extract_transcript_data"})
    return GraduateWorkflowState(data=new_data, context=state.context, history=new_history)


@async_retry()
async def verify_transcript(state: GraduateWorkflowState) -> GraduateWorkflowState:
    verifier = get_transcript_verification_provider()
    ok = verifier.verify_transcript(state.data.get("application_id", ""))
    force_fail = bool(state.data.get("force_transcript_failure", False))
    if force_fail:
        ok = False
    new_data = merge_state(
        state.data,
        {"transcript_verified": ok, "status": ApplicationStatus.TRANSCRIPT_VERIFIED},
    )
    new_history = append_history_entry(state.history, {"step": "verify_transcript"})
    return GraduateWorkflowState(data=new_data, context=state.context, history=new_history)


def decide_after_transcript(state: GraduateWorkflowState) -> str:
    if not state.data.get("transcript_verified", True):
        return "finalize_decision"
    return "route_to_department"


def route_to_department(state: GraduateWorkflowState) -> GraduateWorkflowState:
    new_data = merge_state(state.data, {"status": ApplicationStatus.ROUTED_TO_DEPARTMENT})
    new_history = append_history_entry(state.history, {"step": "route_to_department"})
    return GraduateWorkflowState(data=new_data, context=state.context, history=new_history)


def evaluate_department_fit(state: GraduateWorkflowState) -> GraduateWorkflowState:
    settings = get_settings()
    gat = float(state.data.get("gat_score") or 0.0)
    transcript = float(state.data.get("transcript_score") or 0.0)
    score = (gat * settings.gat_weight) + (transcript * settings.transcript_weight)
    new_data = merge_state(
        state.data,
        {"department_score": score, "status": ApplicationStatus.DEPARTMENT_REVIEW},
    )
    new_history = append_history_entry(state.history, {"step": "evaluate_department_fit"})
    return GraduateWorkflowState(data=new_data, context=state.context, history=new_history)


def apply_capacity_filter(state: GraduateWorkflowState) -> GraduateWorkflowState:
    settings = get_settings()
    force_fail = bool(state.data.get("force_capacity_failure", False))
    capacity_ok = not force_fail
    new_data = merge_state(
        state.data,
        {"capacity_ok": capacity_ok},
    )
    new_history = append_history_entry(state.history, {"step": "apply_capacity_filter"})
    return GraduateWorkflowState(data=new_data, context=state.context, history=new_history)


def decide_next_after_capacity(state: GraduateWorkflowState) -> str:
    if state.data.get("capacity_ok"):
        return "create_department_review_checkpoint"
    return "finalize_decision"


def create_department_review_checkpoint(state: GraduateWorkflowState) -> GraduateWorkflowState:
    new_history = append_history_entry(state.history, {"step": "create_department_review_checkpoint"})
    return GraduateWorkflowState(data=state.data, context=state.context, history=new_history)


def create_registrar_review_checkpoint(state: GraduateWorkflowState) -> GraduateWorkflowState:
    new_history = append_history_entry(state.history, {"step": "create_registrar_review_checkpoint"})
    return GraduateWorkflowState(data=state.data, context=state.context, history=new_history)


def finalize_decision(state: GraduateWorkflowState) -> GraduateWorkflowState:
    payment_ok = bool(state.data.get("payment_verified", True))
    transcript_ok = bool(state.data.get("transcript_verified", True))
    capacity_ok = bool(state.data.get("capacity_ok", True))
    status = ApplicationStatus.APPROVED if (payment_ok and transcript_ok and capacity_ok) else ApplicationStatus.REJECTED
    new_data = merge_state(state.data, {"status": status})
    new_history = append_history_entry(state.history, {"step": "finalize_decision"})
    return GraduateWorkflowState(data=new_data, context=state.context, history=new_history)


def trigger_onboarding(state: GraduateWorkflowState) -> GraduateWorkflowState:
    new_data = merge_state(state.data, {"status": ApplicationStatus.ENROLLED})
    new_history = append_history_entry(state.history, {"step": "trigger_onboarding"})
    return GraduateWorkflowState(data=new_data, context=state.context, history=new_history)


def build_graduate_main_graph() -> Any:
    graph = StateGraph(GraduateWorkflowState)

    graph.add_node("load_application", load_application)
    graph.add_node("validate_fields", validate_fields)
    graph.add_node("verify_payment", verify_payment)
    graph.add_node("fetch_gat_result", fetch_gat_result)
    graph.add_node("extract_transcript_data", extract_transcript_data)
    graph.add_node("verify_transcript", verify_transcript)
    graph.add_node("route_to_department", route_to_department)
    graph.add_node("evaluate_department_fit", evaluate_department_fit)
    graph.add_node("apply_capacity_filter", apply_capacity_filter)
    graph.add_node("create_department_review_checkpoint", create_department_review_checkpoint)
    graph.add_node("create_registrar_review_checkpoint", create_registrar_review_checkpoint)
    graph.add_node("finalize_decision", finalize_decision)
    graph.add_node("trigger_onboarding", trigger_onboarding)

    graph.set_entry_point("load_application")

    graph.add_edge("load_application", "validate_fields")
    graph.add_conditional_edges(
        "verify_payment",
        decide_after_payment,
        {
            "fetch_gat_result": "fetch_gat_result",
            "finalize_decision": "finalize_decision",
        },
    )
    graph.add_edge("validate_fields", "verify_payment")
    graph.add_edge("fetch_gat_result", "extract_transcript_data")
    graph.add_edge("extract_transcript_data", "verify_transcript")
    graph.add_conditional_edges(
        "verify_transcript",
        decide_after_transcript,
        {
            "route_to_department": "route_to_department",
            "finalize_decision": "finalize_decision",
        },
    )
    graph.add_edge("route_to_department", "evaluate_department_fit")
    graph.add_edge("evaluate_department_fit", "apply_capacity_filter")
    graph.add_conditional_edges(
        "apply_capacity_filter",
        decide_next_after_capacity,
        {
            "create_department_review_checkpoint": "create_department_review_checkpoint",
            "finalize_decision": "finalize_decision",
        },
    )
    graph.add_edge("create_department_review_checkpoint", "create_registrar_review_checkpoint")
    graph.add_edge("create_registrar_review_checkpoint", "finalize_decision")
    graph.add_edge("finalize_decision", "trigger_onboarding")

    return graph.compile()

