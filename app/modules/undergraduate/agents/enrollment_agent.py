"""
Enrollment & Onboarding Agent — LangGraph pipeline.

3-node graph:
  gather_admitted → generate_credentials → assign_and_finalize

Processes all DECIDED + ADMIT applications and produces enrollment
records with university IDs, temporary passwords, and section assignments.
"""

from dataclasses import dataclass, field
from typing import Any, Optional

from langgraph.graph import StateGraph, END

AGENT_VERSION = "enrollment-agent-v1"


# ══════════════════════════════════════════════════════════════
#  Data structures
# ══════════════════════════════════════════════════════════════

@dataclass
class AdmittedStudent:
    """One admitted student to be enrolled."""
    application_id: Any  # uuid.UUID
    applicant_id: Any
    admission_number: str
    admission_term: str
    sponsorship_type: str
    stream: str

    # From ranking result
    assigned_program_id: Optional[Any] = None
    assigned_program_name: Optional[str] = None
    assigned_program_code: Optional[str] = None
    assigned_department: Optional[str] = None

    # Generated during enrollment. Section/portal_password moved to
    # course-management — see AcademicSchedulingAgent and
    # OnboardingService respectively.
    university_id: Optional[str] = None


class EnrollmentState(dict):
    """TypedDict-style state for the enrollment graph."""

    @property
    def students(self) -> list[AdmittedStudent]:
        return self.get("students", [])

    @property
    def id_counter_start(self) -> int:
        return self.get("id_counter_start", 1000)

    @property
    def year_suffix(self) -> str:
        return self.get("year_suffix", "26")

    @property
    def section_capacity(self) -> int:
        return self.get("section_capacity", 50)

    @property
    def traces(self) -> list[dict]:
        return self.get("traces", [])


# ══════════════════════════════════════════════════════════════
#  Nodes
# ══════════════════════════════════════════════════════════════

def gather_admitted(state: dict) -> dict:
    """Node 1: Validate the list of admitted students."""
    students = state.get("students", [])
    traces = state.get("traces", [])
    traces.append({
        "step_name": "gather_admitted",
        "reasoning_log": f"Received {len(students)} admitted students for enrollment.",
    })
    return {**state, "traces": traces}


def generate_credentials(state: dict) -> dict:
    """
    Node 2: Generate university ID and temporary portal password
    for each student.

    ID format: UGR/{sequential}/{year_suffix}.

    Section assignment and portal-password issuance moved out of the
    admission module:
      - Section is now a course-management concern handled by the
        AcademicSchedulingAgent at term-open time, based on actual
        registered students per (department, semester).
      - Portal credentials are issued by OnboardingService (4-digit
        PIN + must_change_password) when the officer onboards the
        student into the course-management portal.
    """
    students = state.get("students", [])
    counter = state.get("id_counter_start", 1000)
    year_suffix = state.get("year_suffix", "26")
    traces = state.get("traces", [])

    for s in students:
        s.university_id = f"UGR/{counter}/{year_suffix}"
        counter += 1

    traces.append({
        "step_name": "generate_credentials",
        "reasoning_log": (
            f"Generated university IDs from UGR/{state.get('id_counter_start', 1000)}/{year_suffix} "
            f"to UGR/{counter - 1}/{year_suffix}. Portal credentials are "
            "issued separately by course-management onboarding."
        ),
    })
    return {**state, "students": students, "traces": traces}


def assign_and_finalize(state: dict) -> dict:
    """
    Node 3: Finalisation — no-op for section assignment.

    Section assignment moved to course-management. This node remains
    for graph compatibility and to keep the trace chronology readable;
    it adds a single trace entry recording the handoff.
    """
    students = state.get("students", [])
    traces = state.get("traces", [])
    traces.append({
        "step_name": "assign_and_finalize",
        "reasoning_log": (
            f"Finalised {len(students)} enrollments. Section "
            "allocation will be performed by the AcademicSchedulingAgent "
            "after the course-management term opens."
        ),
    })
    return {**state, "students": students, "traces": traces}


# ══════════════════════════════════════════════════════════════
#  Graph builder
# ══════════════════════════════════════════════════════════════

def build_enrollment_graph():
    """Build and compile the enrollment LangGraph."""
    graph = StateGraph(dict)

    graph.add_node("gather_admitted", gather_admitted)
    graph.add_node("generate_credentials", generate_credentials)
    graph.add_node("assign_and_finalize", assign_and_finalize)

    graph.set_entry_point("gather_admitted")
    graph.add_edge("gather_admitted", "generate_credentials")
    graph.add_edge("generate_credentials", "assign_and_finalize")
    graph.add_edge("assign_and_finalize", END)

    return graph.compile()
