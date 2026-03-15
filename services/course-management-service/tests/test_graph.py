from __future__ import annotations

from agent_core.state import GraphContext

from app.graphs.course_management_main_graph import build_course_management_main_graph
from app.state.models import CourseWorkflowState


def test_course_management_graph_happy_path():
    graph = build_course_management_main_graph()
    initial = CourseWorkflowState(
        data={"registration_id": "reg-1", "student_id": "student-1", "term": "2026-FALL"},
        context=GraphContext(),
        history=[],
    )
    result = graph.invoke(initial)
    steps = [h["step"] for h in result.history]
    # Ensure the full sequence of steps ran.
    assert steps[0] == "load_registration"
    assert "generate_academic_record" in steps

