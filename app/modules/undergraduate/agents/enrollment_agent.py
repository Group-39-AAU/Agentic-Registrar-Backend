"""
Enrollment & Onboarding Agent — LangGraph pipeline.

3-node graph:
  gather_admitted → generate_credentials → assign_and_finalize

Processes all DECIDED + ADMIT applications and produces enrollment
records with university IDs, temporary passwords, and section assignments.
"""

import secrets
import string
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

    # Generated during enrollment
    university_id: Optional[str] = None
    portal_password: Optional[str] = None
    section: Optional[str] = None


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

    ID format: UGR/{sequential}/{year_suffix}
    Password: random 10-char alphanumeric
    """
    students = state.get("students", [])
    counter = state.get("id_counter_start", 1000)
    year_suffix = state.get("year_suffix", "26")
    traces = state.get("traces", [])

    alphabet = string.ascii_letters + string.digits

    for s in students:
        s.university_id = f"UGR/{counter}/{year_suffix}"
        s.portal_password = "".join(secrets.choice(alphabet) for _ in range(10))
        counter += 1

    traces.append({
        "step_name": "generate_credentials",
        "reasoning_log": (
            f"Generated university IDs from UGR/{state.get('id_counter_start', 1000)}/{year_suffix} "
            f"to UGR/{counter - 1}/{year_suffix} and temporary passwords."
        ),
    })
    return {**state, "students": students, "traces": traces}


def assign_and_finalize(state: dict) -> dict:
    """
    Node 3: Assign sections to students.

    Sections are assigned per-program (self-sponsored) or per-stream
    (government). Each section holds up to `section_capacity` students.
    """
    students = state.get("students", [])
    section_capacity = state.get("section_capacity", 50)
    traces = state.get("traces", [])

    # Group students by their program or stream for section assignment
    group_counters: dict[str, int] = {}  # group_key → count

    for s in students:
        group_key = str(s.assigned_program_id) if s.assigned_program_id else s.stream
        count = group_counters.get(group_key, 0)

        # Section letter: A=0, B=1, ...
        section_index = count // section_capacity
        s.section = chr(ord("A") + section_index)

        group_counters[group_key] = count + 1

    traces.append({
        "step_name": "assign_and_finalize",
        "reasoning_log": f"Assigned sections for {len(students)} students across {len(group_counters)} groups.",
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
