from __future__ import annotations

from agent_core.state import GraphContext

from app.domain.enums import ApplicationStatus
from app.graphs.undergrad_main_graph import build_undergrad_main_graph
from app.state.models import UndergradWorkflowState


def _run(initial_data: dict) -> UndergradWorkflowState:
    graph = build_undergrad_main_graph()
    state = UndergradWorkflowState(data=initial_data, context=GraphContext(), history=[])
    return graph.invoke(state)


def test_undergrad_happy_path():
    result = _run({"application_id": "app-1", "status": ApplicationStatus.DRAFT})
    assert result.data["status"] == ApplicationStatus.ONBOARDED
    steps = [h["step"] for h in result.history]
    assert "verify_payment" in steps
    assert "finalize_decision" in steps


def test_undergrad_payment_failure_path():
    result = _run(
        {
            "application_id": "app-2",
            "status": ApplicationStatus.DRAFT,
            "force_payment_failure": True,
        }
    )
    assert result.data["is_payment_valid"] is False
    assert result.data["status"] == ApplicationStatus.REJECTED


def test_undergrad_moe_mismatch_human_review_path():
    result = _run(
        {
            "application_id": "app-3",
            "status": ApplicationStatus.DRAFT,
            "force_moe_mismatch": True,
        }
    )
    assert result.data["status"] in {ApplicationStatus.FLAGGED_FOR_REVIEW, ApplicationStatus.REJECTED}
    steps = [h["step"] for h in result.history]
    assert "create_human_review_checkpoint" in steps

