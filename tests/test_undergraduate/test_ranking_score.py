"""
Ranking Agent — score calculation and ranking logic tests.

Tests the two core LangGraph node functions that implement the
merit-ranking algorithm without touching the DB or any I/O:

  calculate_scores  : final = round((grade12/600)*50 + (uat/100)*50, 2)
  rank_applicants   : split by sponsorship, sort descending by
                      (final_score, uat_score), assign sequential ranks
"""
from __future__ import annotations

import uuid

import pytest

from app.modules.undergraduate.agents.ranking_agent import (
    ApplicantData,
    RankingState,
    calculate_scores,
    rank_applicants,
)
from app.shared.enums import SponsorshipType


# ── Helpers ──────────────────────────────────────────────────────


def _applicant(
    *,
    sponsorship: str = SponsorshipType.SELF_SPONSORED.value,
    grade12: float = 500.0,
    uat: float = 80.0,
    stream: str = "NATURAL",
    admission_number: str = "ADM-001",
) -> ApplicantData:
    return ApplicantData(
        application_id=uuid.uuid4(),
        applicant_id=uuid.uuid4(),
        sponsorship_type=sponsorship,
        stream=stream,
        admission_number=admission_number,
        grade12_score=grade12,
        uat_score=uat,
    )


def _state(*applicants: ApplicantData) -> RankingState:
    return RankingState(applicants=list(applicants))


# ── calculate_scores ──────────────────────────────────────────────


def test_perfect_scores_give_100():
    """grade12=600 and uat=100 → full 50+50=100."""
    a = _applicant(grade12=600.0, uat=100.0)
    state = calculate_scores(_state(a))
    assert state.applicants[0].final_score == 100.0


def test_zero_scores_give_zero():
    a = _applicant(grade12=0.0, uat=0.0)
    state = calculate_scores(_state(a))
    assert state.applicants[0].final_score == 0.0


def test_half_max_each_component_gives_50():
    """grade12=300 (half of 600) + uat=50 (half of 100) → 25+25=50."""
    a = _applicant(grade12=300.0, uat=50.0)
    state = calculate_scores(_state(a))
    assert state.applicants[0].final_score == 50.0


def test_score_is_rounded_to_two_decimal_places():
    a = _applicant(grade12=400.0, uat=75.0)
    state = calculate_scores(_state(a))
    score = state.applicants[0].final_score
    assert score == round(score, 2)


def test_calculate_scores_updates_every_applicant():
    applicants = [
        _applicant(grade12=600.0, uat=100.0, admission_number="A1"),
        _applicant(grade12=300.0, uat=50.0,  admission_number="A2"),
        _applicant(grade12=0.0,   uat=0.0,   admission_number="A3"),
    ]
    state = calculate_scores(_state(*applicants))
    scores = [a.final_score for a in state.applicants]
    assert scores == [100.0, 50.0, 0.0]


def test_calculate_scores_adds_trace():
    state = calculate_scores(_state(_applicant()))
    assert any(t["step_name"] == "calculate_scores" for t in state.traces)


# ── rank_applicants ───────────────────────────────────────────────


def test_rank_applicants_splits_by_sponsorship():
    ss = _applicant(sponsorship=SponsorshipType.SELF_SPONSORED.value, admission_number="SS")
    gov = _applicant(sponsorship=SponsorshipType.GOVERNMENT.value, admission_number="GOV")
    ss.final_score = 73.33
    gov.final_score = 72.50

    state = rank_applicants(RankingState(applicants=[ss, gov]))
    assert len(state.self_sponsored) == 1
    assert len(state.government) == 1
    assert state.self_sponsored[0].admission_number == "SS"
    assert state.government[0].admission_number == "GOV"


def test_rank_applicants_orders_descending_by_final_score():
    low = _applicant(admission_number="LOW")
    high = _applicant(admission_number="HIGH")
    low.final_score = 40.0
    high.final_score = 90.0

    state = rank_applicants(RankingState(applicants=[low, high]))
    ranked = state.self_sponsored
    assert ranked[0].final_score > ranked[1].final_score
    assert ranked[0].admission_number == "HIGH"


def test_rank_applicants_assigns_sequential_positions():
    applicants = []
    for i, score in enumerate([90.0, 80.0, 70.0], start=1):
        a = _applicant(admission_number=f"A{i}")
        a.final_score = score
        applicants.append(a)

    state = rank_applicants(RankingState(applicants=applicants))
    positions = [a.rank_position for a in state.self_sponsored]
    assert positions == [1, 2, 3]


def test_rank_applicants_tiebreak_by_uat_score():
    """When final scores are equal, higher UAT score wins the higher rank."""
    high_uat = _applicant(admission_number="HIGH-UAT", uat=90.0)
    low_uat  = _applicant(admission_number="LOW-UAT",  uat=70.0)
    high_uat.final_score = 75.0
    low_uat.final_score  = 75.0

    state = rank_applicants(RankingState(applicants=[low_uat, high_uat]))
    assert state.self_sponsored[0].admission_number == "HIGH-UAT"


def test_rank_applicants_government_ranked_separately():
    """Government and self-sponsored pools each get rank 1 independently."""
    ss  = _applicant(sponsorship=SponsorshipType.SELF_SPONSORED.value, admission_number="SS")
    gov = _applicant(sponsorship=SponsorshipType.GOVERNMENT.value, admission_number="GOV")
    ss.final_score = 85.0
    gov.final_score = 70.0

    state = rank_applicants(RankingState(applicants=[ss, gov]))
    assert state.self_sponsored[0].rank_position == 1
    assert state.government[0].rank_position == 1


def test_rank_applicants_adds_trace():
    a = _applicant()
    a.final_score = 75.0
    state = rank_applicants(RankingState(applicants=[a]))
    assert any(t["step_name"] == "rank_applicants" for t in state.traces)
