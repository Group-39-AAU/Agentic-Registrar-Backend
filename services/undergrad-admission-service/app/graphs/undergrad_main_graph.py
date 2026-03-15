from __future__ import annotations

from typing import Any, Dict

from langgraph.graph import StateGraph

from agent_core.state import append_history_entry, merge_state

from ..domain.enums import ApplicationStatus
from ..state.models import UndergradWorkflowState


def load_application(state: UndergradWorkflowState) -> UndergradWorkflowState:
    new_history = append_history_entry(state.history, {"step": "load_application"})
    return UndergradWorkflowState(data=state.data, context=state.context, history=new_history)


def validate_completeness(state: UndergradWorkflowState) -> UndergradWorkflowState:
    new_data = merge_state(state.data, {"status": ApplicationStatus.VALIDATION_PENDING})
    new_history = append_history_entry(state.history, {"step": "validate_completeness"})
    return UndergradWorkflowState(data=new_data, context=state.context, history=new_history)


def verify_payment(state: UndergradWorkflowState) -> UndergradWorkflowState:
    force_fail = bool(state.data.get("force_payment_failure", False))
    is_valid = not force_fail
    new_data = merge_state(
        state.data,
        {"is_payment_valid": is_valid, "status": ApplicationStatus.VERIFICATION_PENDING},
    )
    new_history = append_history_entry(state.history, {"step": "verify_payment"})
    return UndergradWorkflowState(data=new_data, context=state.context, history=new_history)


def decide_after_payment(state: UndergradWorkflowState) -> str:
    if not state.data.get("is_payment_valid", True):
        return "finalize_decision"
    return "extract_document_data"


def extract_document_data(state: UndergradWorkflowState) -> UndergradWorkflowState:
    has_issue = bool(state.data.get("force_extraction_issue", False))
    new_data = merge_state(state.data, {"has_extraction_issue": has_issue})
    new_history = append_history_entry(state.history, {"step": "extract_document_data"})
    return UndergradWorkflowState(data=new_data, context=state.context, history=new_history)


def decide_after_extraction(state: UndergradWorkflowState) -> str:
    if state.data.get("has_extraction_issue"):
        return "create_human_review_checkpoint"
    return "verify_with_moe"


def verify_with_moe(state: UndergradWorkflowState) -> UndergradWorkflowState:
    mismatch = bool(state.data.get("force_moe_mismatch", False))
    verified = not mismatch
    new_data = merge_state(state.data, {"moe_verified": verified})
    new_history = append_history_entry(state.history, {"step": "verify_with_moe"})
    return UndergradWorkflowState(data=new_data, context=state.context, history=new_history)


def decide_after_moe(state: UndergradWorkflowState) -> str:
    if not state.data.get("moe_verified", True):
        return "create_human_review_checkpoint"
    return "fetch_uat_score"


def fetch_uat_score(state: UndergradWorkflowState) -> UndergradWorkflowState:
    new_data = merge_state(state.data, {"uat_score": 80.0})
    new_history = append_history_entry(state.history, {"step": "fetch_uat_score"})
    return UndergradWorkflowState(data=new_data, context=state.context, history=new_history)


def compute_cumulative_score(state: UndergradWorkflowState) -> UndergradWorkflowState:
    uat = float(state.data.get("uat_score") or 0.0)
    cgpa = float(state.data.get("cgpa") or 0.0)
    cumulative = (uat * 0.6) + (cgpa * 0.4)
    new_data = merge_state(state.data, {"cumulative_score": cumulative})
    new_history = append_history_entry(state.history, {"step": "compute_cumulative_score"})
    return UndergradWorkflowState(data=new_data, context=state.context, history=new_history)


def rank_candidate(state: UndergradWorkflowState) -> UndergradWorkflowState:
    new_data = merge_state(
        state.data,
        {"status": ApplicationStatus.RANKED, "ranked_position": 1},
    )
    new_history = append_history_entry(state.history, {"step": "rank_candidate"})
    return UndergradWorkflowState(data=new_data, context=state.context, history=new_history)


def decide_review_path(state: UndergradWorkflowState) -> str:
    flagged = bool(state.data.get("flagged_for_review", False))
    if flagged:
        return "create_human_review_checkpoint"
    return "finalize_decision"


def create_human_review_checkpoint(state: UndergradWorkflowState) -> UndergradWorkflowState:
    new_data = merge_state(
        state.data,
        {"status": ApplicationStatus.FLAGGED_FOR_REVIEW},
    )
    new_history = append_history_entry(state.history, {"step": "create_human_review_checkpoint"})
    return UndergradWorkflowState(data=new_data, context=state.context, history=new_history)


def finalize_decision(state: UndergradWorkflowState) -> UndergradWorkflowState:
    is_payment_valid = bool(state.data.get("is_payment_valid", True))
    moe_verified = bool(state.data.get("moe_verified", True))
    status = ApplicationStatus.RECOMMENDED if (is_payment_valid and moe_verified) else ApplicationStatus.REJECTED
    new_data = merge_state(
        state.data,
        {"status": status},
    )
    new_history = append_history_entry(state.history, {"step": "finalize_decision"})
    return UndergradWorkflowState(data=new_data, context=state.context, history=new_history)


def trigger_onboarding(state: UndergradWorkflowState) -> UndergradWorkflowState:
    new_data = merge_state(
        state.data,
        {"status": ApplicationStatus.ONBOARDED},
    )
    new_history = append_history_entry(state.history, {"step": "trigger_onboarding"})
    return UndergradWorkflowState(data=new_data, context=state.context, history=new_history)


def build_undergrad_main_graph() -> Any:
    graph = StateGraph(UndergradWorkflowState)
    graph.add_node("load_application", load_application)
    graph.add_node("validate_completeness", validate_completeness)
    graph.add_node("verify_payment", verify_payment)
    graph.add_node("extract_document_data", extract_document_data)
    graph.add_node("verify_with_moe", verify_with_moe)
    graph.add_node("fetch_uat_score", fetch_uat_score)
    graph.add_node("compute_cumulative_score", compute_cumulative_score)
    graph.add_node("rank_candidate", rank_candidate)
    graph.add_node("create_human_review_checkpoint", create_human_review_checkpoint)
    graph.add_node("finalize_decision", finalize_decision)
    graph.add_node("trigger_onboarding", trigger_onboarding)

    graph.set_entry_point("load_application")

    graph.add_edge("load_application", "validate_completeness")
    graph.add_conditional_edges(
        "verify_payment",
        decide_after_payment,
        {
            "extract_document_data": "extract_document_data",
            "finalize_decision": "finalize_decision",
        },
    )
    graph.add_conditional_edges(
        "extract_document_data",
        decide_after_extraction,
        {
            "verify_with_moe": "verify_with_moe",
            "create_human_review_checkpoint": "create_human_review_checkpoint",
        },
    )
    graph.add_conditional_edges(
        "verify_with_moe",
        decide_after_moe,
        {
            "fetch_uat_score": "fetch_uat_score",
            "create_human_review_checkpoint": "create_human_review_checkpoint",
        },
    )
    graph.add_edge("validate_completeness", "verify_payment")
    graph.add_edge("fetch_uat_score", "compute_cumulative_score")
    graph.add_edge("compute_cumulative_score", "rank_candidate")
    graph.add_conditional_edges(
        "rank_candidate",
        decide_review_path,
        {
            "create_human_review_checkpoint": "create_human_review_checkpoint",
            "finalize_decision": "finalize_decision",
        },
    )
    graph.add_edge("create_human_review_checkpoint", "finalize_decision")
    graph.add_edge("finalize_decision", "trigger_onboarding")

    return graph.compile()

