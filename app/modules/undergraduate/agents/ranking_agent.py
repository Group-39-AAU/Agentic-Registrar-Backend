"""
Eligibility & Ranking Agent — LangGraph pipeline.

Nodes:
    1. gather_data       — Fetch UAT_COMPLETED applications, MoE records, UAT records
    2. calculate_scores  — Compute final score = (grade12/600)*50 + (uat/100)*50
    3. rank_applicants   — Split by category, sort desc, assign rank positions
    4. allocate_seats    — Greedy merit-based assignment (P1→P2→P3 / stream)
    5. finalize          — Persist RankingResult rows and update cutoff scores
"""

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from langgraph.graph import END, StateGraph

from app.core.logging import get_logger

logger = get_logger("ai.ranking_agent")

AGENT_VERSION = "ranking-agent-v1.0"


# ── Agent State ──────────────────────────────────────────────

@dataclass
class ApplicantData:
    """Data for one applicant gathered from multiple tables."""
    application_id: uuid.UUID
    applicant_id: uuid.UUID
    sponsorship_type: str  # SELF_SPONSORED / GOVERNMENT
    stream: str
    admission_number: str
    program_choice_1_id: Optional[uuid.UUID] = None
    program_choice_2_id: Optional[uuid.UUID] = None
    program_choice_3_id: Optional[uuid.UUID] = None
    grade12_score: float = 0.0
    uat_score: float = 0.0
    final_score: float = 0.0
    rank_position: int = 0
    assigned_program_id: Optional[uuid.UUID] = None
    assigned_stream: Optional[str] = None
    is_assigned: bool = False
    assignment_detail: str = ""


@dataclass
class RankingState:
    """Mutable state passed through LangGraph nodes."""
    batch_id: str = ""

    # Raw data
    applicants: list[ApplicantData] = field(default_factory=list)
    program_capacities: dict = field(default_factory=dict)   # {program_id: max_capacity}
    program_info: dict = field(default_factory=dict)          # {program_id: {code, name, stream}}
    stream_quotas: dict = field(default_factory=dict)         # {stream: max_capacity}

    # Ranked lists
    self_sponsored: list[ApplicantData] = field(default_factory=list)
    government: list[ApplicantData] = field(default_factory=list)

    # Traces
    traces: list[dict] = field(default_factory=list)
    error: str = ""


# ── Node 1: Gather Data ─────────────────────────────────────

def gather_data(state: RankingState) -> RankingState:
    """
    This node is a placeholder — the actual DB fetching happens
    in the router before invoking the graph, because LangGraph
    nodes are synchronous but our DB layer is async.
    The data is pre-loaded into state.applicants, program_capacities, etc.
    """
    reasoning = f"Gathered {len(state.applicants)} applicants for ranking."
    state.traces.append({
        "step_name": "gather_data",
        "reasoning_log": reasoning,
        "result": "OK",
    })
    logger.info("Ranking: gathered %d applicants", len(state.applicants))
    return state


# ── Node 2: Calculate Scores ────────────────────────────────

def calculate_scores(state: RankingState) -> RankingState:
    """Compute final score = (grade12/600)*50 + (uat/100)*50."""
    reasoning_lines = []

    for a in state.applicants:
        a.final_score = round((a.grade12_score / 600) * 50 + (a.uat_score / 100) * 50, 2)
        reasoning_lines.append(
            f"  {a.admission_number}: G12={a.grade12_score}, UAT={a.uat_score} → Final={a.final_score}"
        )

    state.traces.append({
        "step_name": "calculate_scores",
        "reasoning_log": f"Calculated final scores for {len(state.applicants)} applicants.\n"
                         + "\n".join(reasoning_lines[:20])  # Truncate for readability
                         + (f"\n  ... and {len(reasoning_lines) - 20} more" if len(reasoning_lines) > 20 else ""),
        "result": "OK",
    })
    logger.info("Ranking: calculated scores for %d applicants", len(state.applicants))
    return state


# ── Node 3: Rank Applicants ─────────────────────────────────

def rank_applicants(state: RankingState) -> RankingState:
    """Split by category, sort desc by final_score (tie-break: UAT), assign ranks."""
    self_sponsored = [a for a in state.applicants if a.sponsorship_type == "SELF_SPONSORED"]
    government = [a for a in state.applicants if a.sponsorship_type == "GOVERNMENT"]

    # Sort: descending final_score, then descending uat_score for tie-break
    self_sponsored.sort(key=lambda a: (a.final_score, a.uat_score), reverse=True)
    government.sort(key=lambda a: (a.final_score, a.uat_score), reverse=True)

    # Assign rank positions
    for i, a in enumerate(self_sponsored, 1):
        a.rank_position = i
    for i, a in enumerate(government, 1):
        a.rank_position = i

    state.self_sponsored = self_sponsored
    state.government = government

    state.traces.append({
        "step_name": "rank_applicants",
        "reasoning_log": (
            f"Ranked {len(self_sponsored)} self-sponsored and "
            f"{len(government)} government-sponsored applicants.\n"
            f"Top self-sponsored: {self_sponsored[0].final_score if self_sponsored else 'N/A'}\n"
            f"Top government: {government[0].final_score if government else 'N/A'}"
        ),
        "result": "OK",
    })
    logger.info(
        "Ranking: %d self-sponsored, %d government",
        len(self_sponsored), len(government),
    )
    return state


# ── Node 4: Allocate Seats ──────────────────────────────────

def allocate_seats(state: RankingState) -> RankingState:
    """
    Greedy merit-based allocation:
    - Self-sponsored: try P1 → P2 → P3 respecting max_capacity
    - Government: assign to stream respecting stream quota
    """
    reasoning_lines = []

    # ── Self-sponsored allocation ──
    # Track remaining capacity per program
    remaining_capacity = dict(state.program_capacities)  # {program_id: remaining}
    assigned_ss = 0
    unassigned_ss = 0

    for a in state.self_sponsored:
        placed = False
        for choice_num, prog_id in enumerate(
            [a.program_choice_1_id, a.program_choice_2_id, a.program_choice_3_id], 1
        ):
            if prog_id is None:
                continue
            cap = remaining_capacity.get(prog_id, 0)
            if cap > 0:
                a.assigned_program_id = prog_id
                a.is_assigned = True
                remaining_capacity[prog_id] = cap - 1
                prog_info = state.program_info.get(str(prog_id), {})
                prog_name = prog_info.get("name", str(prog_id))
                a.assignment_detail = f"Assigned to P{choice_num}: {prog_name}"
                placed = True
                assigned_ss += 1
                break

        if not placed:
            a.is_assigned = False
            a.assignment_detail = "Unassigned — all preferred programs full"
            unassigned_ss += 1

    reasoning_lines.append(
        f"Self-sponsored: {assigned_ss} assigned, {unassigned_ss} unassigned"
    )

    # ── Government-sponsored allocation ──
    remaining_stream = dict(state.stream_quotas)  # {stream_str: remaining}
    assigned_gov = 0
    unassigned_gov = 0

    for a in state.government:
        stream_key = a.stream
        cap = remaining_stream.get(stream_key, 0)
        if cap > 0:
            a.assigned_stream = stream_key
            a.is_assigned = True
            remaining_stream[stream_key] = cap - 1
            a.assignment_detail = f"Assigned to {stream_key} stream"
            assigned_gov += 1
        else:
            a.is_assigned = False
            a.assignment_detail = f"Unassigned — {stream_key} stream full"
            unassigned_gov += 1

    reasoning_lines.append(
        f"Government: {assigned_gov} assigned, {unassigned_gov} unassigned"
    )

    state.traces.append({
        "step_name": "allocate_seats",
        "reasoning_log": "\n".join(reasoning_lines),
        "result": "OK",
    })
    logger.info(
        "Ranking allocation: SS=%d/%d, GOV=%d/%d",
        assigned_ss, assigned_ss + unassigned_ss,
        assigned_gov, assigned_gov + unassigned_gov,
    )
    return state


# ── Node 5: Finalize ────────────────────────────────────────

def finalize(state: RankingState) -> RankingState:
    """
    Summary node — the actual DB writes happen in the router
    after the graph completes (async). This just logs final stats.
    """
    all_applicants = state.self_sponsored + state.government
    assigned = sum(1 for a in all_applicants if a.is_assigned)
    unassigned = len(all_applicants) - assigned

    state.traces.append({
        "step_name": "finalize",
        "reasoning_log": (
            f"Ranking complete. Batch: {state.batch_id}\n"
            f"Total: {len(all_applicants)}, Assigned: {assigned}, Unassigned: {unassigned}"
        ),
        "result": "OK",
    })
    logger.info(
        "Ranking finalized: batch=%s, total=%d, assigned=%d, unassigned=%d",
        state.batch_id, len(all_applicants), assigned, unassigned,
    )
    return state


# ── Graph Builder ────────────────────────────────────────────

def build_ranking_graph() -> StateGraph:
    """
    Construct the LangGraph StateGraph for the Eligibility & Ranking Agent.

    Flow: gather_data → calculate_scores → rank_applicants → allocate_seats → finalize → END
    """
    graph = StateGraph(RankingState)

    graph.add_node("gather_data", gather_data)
    graph.add_node("calculate_scores", calculate_scores)
    graph.add_node("rank_applicants", rank_applicants)
    graph.add_node("allocate_seats", allocate_seats)
    graph.add_node("finalize", finalize)

    graph.set_entry_point("gather_data")
    graph.add_edge("gather_data", "calculate_scores")
    graph.add_edge("calculate_scores", "rank_applicants")
    graph.add_edge("rank_applicants", "allocate_seats")
    graph.add_edge("allocate_seats", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile()
