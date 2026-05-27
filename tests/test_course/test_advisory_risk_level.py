"""
AcademicAdvisoryAgent.flag_risk_level — pure-function tests.

Validates every branch of the three-tier risk classifier in isolation,
using the SDS Table 74 thresholds baked into the agent class:

    HIGH   CGPA < 2.0, OR (CGPA < 2.75 AND proposed load >= 18 ECTS)
    MEDIUM CGPA < 2.75, OR proposed load >= 18 ECTS
    LOW    otherwise

Also covers approve_course_load, which is the downstream gate that
translates a RiskStatus verdict into a bool and escalates HIGH risk
to officer review.
"""
from __future__ import annotations

import pytest

from app.modules.course.agents.academic_advisory_agent import AcademicAdvisoryAgent
from app.shared.enums import RiskStatus


@pytest.fixture
def agent() -> AcademicAdvisoryAgent:
    return AcademicAdvisoryAgent(agent_id="AGENT_AAA_TEST")


# ── HIGH risk ─────────────────────────────────────────────────────


def test_cgpa_below_2_is_high_regardless_of_load(agent):
    assert agent.flag_risk_level(cgpa=1.99, total_credits=12) is RiskStatus.HIGH


def test_cgpa_exactly_zero_is_high(agent):
    assert agent.flag_risk_level(cgpa=0.0, total_credits=0) is RiskStatus.HIGH


def test_cgpa_at_floor_boundary_1_99_is_high(agent):
    assert agent.flag_risk_level(cgpa=1.99, total_credits=0) is RiskStatus.HIGH


def test_medium_zone_cgpa_with_heavy_load_escalates_to_high(agent):
    """CGPA in [2.0, 2.75) AND load >= 18 ECTS → HIGH."""
    assert agent.flag_risk_level(cgpa=2.50, total_credits=18) is RiskStatus.HIGH


def test_medium_zone_cgpa_with_load_at_threshold_is_high(agent):
    assert agent.flag_risk_level(cgpa=2.74, total_credits=18) is RiskStatus.HIGH


# ── MEDIUM risk ───────────────────────────────────────────────────


def test_cgpa_in_medium_zone_light_load_is_medium(agent):
    """CGPA in [2.0, 2.75) with load < 18 → MEDIUM."""
    assert agent.flag_risk_level(cgpa=2.50, total_credits=15) is RiskStatus.MEDIUM


def test_healthy_cgpa_with_heavy_load_is_medium(agent):
    """CGPA >= 2.75 but load >= 18 ECTS → MEDIUM (load penalty alone)."""
    assert agent.flag_risk_level(cgpa=3.00, total_credits=18) is RiskStatus.MEDIUM


def test_cgpa_exactly_2_light_load_is_medium(agent):
    """Boundary: CGPA == 2.0 (no longer < 2.0) with normal load → MEDIUM."""
    assert agent.flag_risk_level(cgpa=2.0, total_credits=15) is RiskStatus.MEDIUM


# ── LOW risk ──────────────────────────────────────────────────────


def test_high_cgpa_light_load_is_low(agent):
    assert agent.flag_risk_level(cgpa=3.80, total_credits=12) is RiskStatus.LOW


def test_perfect_cgpa_zero_load_is_low(agent):
    assert agent.flag_risk_level(cgpa=4.00, total_credits=0) is RiskStatus.LOW


def test_cgpa_at_2_75_light_load_is_low(agent):
    """Exactly 2.75 with load below 18 falls through to LOW."""
    assert agent.flag_risk_level(cgpa=2.75, total_credits=17) is RiskStatus.LOW


# ── approve_course_load ───────────────────────────────────────────


def test_high_risk_is_not_approvable(agent):
    """HIGH always escalates — the officer must review."""
    assert agent.approve_course_load(RiskStatus.HIGH) is False


def test_medium_risk_is_approvable(agent):
    assert agent.approve_course_load(RiskStatus.MEDIUM) is True


def test_low_risk_is_approvable(agent):
    assert agent.approve_course_load(RiskStatus.LOW) is True


# ── Custom thresholds (constructor overrides) ─────────────────────


def test_custom_thresholds_are_respected():
    """The constructor allows overriding SDS defaults for testing."""
    strict = AcademicAdvisoryAgent(
        agent_id="AGENT_AAA_STRICT",
        high_risk_cgpa=3.0,
        medium_risk_cgpa=3.5,
        high_load_threshold=12,
    )
    # Under strict thresholds a CGPA of 2.8 is HIGH.
    assert strict.flag_risk_level(cgpa=2.8, total_credits=10) is RiskStatus.HIGH
