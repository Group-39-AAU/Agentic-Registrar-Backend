from __future__ import annotations

from agent_core.state import GraphContext

from app.domain.enums import ApplicationStatus
from app.graphs.graduate_main_graph import build_graduate_main_graph
from app.state.models import GraduateWorkflowState


def _run(initial_data: dict) -> GraduateWorkflowState:
    graph = build_graduate_main_graph()
    state = GraduateWorkflowState(data=initial_data, context=GraphContext(), history=[])
    return graph.invoke(state)


def test_graduate_happy_path():
    result = _run({"application_id": "app-1", "applicant_id": "applicant-1", "status": ApplicationStatus.DRAFT})
    assert result.data["status"] == ApplicationStatus.ENROLLED
    steps = [h["step"] for h in result.history]
    assert "verify_payment" in steps
    assert "verify_transcript" in steps
    assert "finalize_decision" in steps


def test_graduate_payment_failure_path():
    result = _run(
        {
            "application_id": "app-2",
            "applicant_id": "applicant-2",
            "status": ApplicationStatus.DRAFT,
            "force_payment_failure": True,
        }
    )
    assert result.data["payment_verified"] is False
    assert result.data["status"] == ApplicationStatus.REJECTED


def test_graduate_transcript_failure_path():
    result = _run(
        {
            "application_id": "app-3",
            "applicant_id": "applicant-3",
            "status": ApplicationStatus.DRAFT,
            "force_transcript_failure": True,
        }
    )
    assert result.data["transcript_verified"] is False
    assert result.data["status"] == ApplicationStatus.REJECTED

