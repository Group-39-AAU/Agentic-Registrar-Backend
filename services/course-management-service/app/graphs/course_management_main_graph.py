from __future__ import annotations

from typing import Any

from langgraph.graph import StateGraph

from agent_core.state import append_history_entry, merge_state

from ..state.models import CourseWorkflowState


def load_registration(state: CourseWorkflowState) -> CourseWorkflowState:
    new_history = append_history_entry(state.history, {"step": "load_registration"})
    return CourseWorkflowState(data=state.data, context=state.context, history=new_history)


def validate_prerequisites(state: CourseWorkflowState) -> CourseWorkflowState:
    new_data = merge_state(state.data, {"validated": True})
    new_history = append_history_entry(state.history, {"step": "validate_prerequisites"})
    return CourseWorkflowState(data=new_data, context=state.context, history=new_history)


def validate_payment_or_cost_sharing(state: CourseWorkflowState) -> CourseWorkflowState:
    new_history = append_history_entry(state.history, {"step": "validate_payment_or_cost_sharing"})
    return CourseWorkflowState(data=state.data, context=state.context, history=new_history)


def evaluate_advisory(state: CourseWorkflowState) -> CourseWorkflowState:
    new_history = append_history_entry(state.history, {"step": "evaluate_advisory"})
    return CourseWorkflowState(data=state.data, context=state.context, history=new_history)


def finalize_registration(state: CourseWorkflowState) -> CourseWorkflowState:
    new_data = merge_state(state.data, {"finalized": True})
    new_history = append_history_entry(state.history, {"step": "finalize_registration"})
    return CourseWorkflowState(data=new_data, context=state.context, history=new_history)


def process_add_drop(state: CourseWorkflowState) -> CourseWorkflowState:
    new_history = append_history_entry(state.history, {"step": "process_add_drop"})
    return CourseWorkflowState(data=state.data, context=state.context, history=new_history)


def monitor_grade_entry(state: CourseWorkflowState) -> CourseWorkflowState:
    new_history = append_history_entry(state.history, {"step": "monitor_grade_entry"})
    return CourseWorkflowState(data=state.data, context=state.context, history=new_history)


def validate_grade_submission(state: CourseWorkflowState) -> CourseWorkflowState:
    new_history = append_history_entry(state.history, {"step": "validate_grade_submission"})
    return CourseWorkflowState(data=state.data, context=state.context, history=new_history)


def create_grade_authorization_checkpoint(state: CourseWorkflowState) -> CourseWorkflowState:
    new_history = append_history_entry(
        state.history,
        {"step": "create_grade_authorization_checkpoint"},
    )
    return CourseWorkflowState(data=state.data, context=state.context, history=new_history)


def compute_academic_status(state: CourseWorkflowState) -> CourseWorkflowState:
    new_history = append_history_entry(state.history, {"step": "compute_academic_status"})
    return CourseWorkflowState(data=state.data, context=state.context, history=new_history)


def create_status_authorization_checkpoint(state: CourseWorkflowState) -> CourseWorkflowState:
    new_history = append_history_entry(
        state.history,
        {"step": "create_status_authorization_checkpoint"},
    )
    return CourseWorkflowState(data=state.data, context=state.context, history=new_history)


def generate_academic_record(state: CourseWorkflowState) -> CourseWorkflowState:
    new_history = append_history_entry(state.history, {"step": "generate_academic_record"})
    return CourseWorkflowState(data=state.data, context=state.context, history=new_history)


def build_course_management_main_graph() -> Any:
    graph = StateGraph(CourseWorkflowState)

    graph.add_node("load_registration", load_registration)
    graph.add_node("validate_prerequisites", validate_prerequisites)
    graph.add_node("validate_payment_or_cost_sharing", validate_payment_or_cost_sharing)
    graph.add_node("evaluate_advisory", evaluate_advisory)
    graph.add_node("finalize_registration", finalize_registration)
    graph.add_node("process_add_drop", process_add_drop)
    graph.add_node("monitor_grade_entry", monitor_grade_entry)
    graph.add_node("validate_grade_submission", validate_grade_submission)
    graph.add_node("create_grade_authorization_checkpoint", create_grade_authorization_checkpoint)
    graph.add_node("compute_academic_status", compute_academic_status)
    graph.add_node("create_status_authorization_checkpoint", create_status_authorization_checkpoint)
    graph.add_node("generate_academic_record", generate_academic_record)

    graph.set_entry_point("load_registration")

    graph.add_edge("load_registration", "validate_prerequisites")
    graph.add_edge("validate_prerequisites", "validate_payment_or_cost_sharing")
    graph.add_edge("validate_payment_or_cost_sharing", "evaluate_advisory")
    graph.add_edge("evaluate_advisory", "finalize_registration")
    graph.add_edge("finalize_registration", "process_add_drop")
    graph.add_edge("process_add_drop", "monitor_grade_entry")
    graph.add_edge("monitor_grade_entry", "validate_grade_submission")
    graph.add_edge("validate_grade_submission", "create_grade_authorization_checkpoint")
    graph.add_edge("create_grade_authorization_checkpoint", "compute_academic_status")
    graph.add_edge("compute_academic_status", "create_status_authorization_checkpoint")
    graph.add_edge("create_status_authorization_checkpoint", "generate_academic_record")

    return graph.compile()

