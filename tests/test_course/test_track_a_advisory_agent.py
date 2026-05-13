"""
Track A — AcademicAdvisoryAgent contract tests.

Drives the agent through every public method against a tight CS
curriculum graph built per-test in SQLite.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.modules.course.agents import (
    AcademicAdvisoryAgent,
    Advice,
    CourseBaseAgent,
    GapAnalysis,
)
from app.modules.course.models import Course
from app.shared.enums import AgentStatus, RiskStatus


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def advisory_agent() -> AcademicAdvisoryAgent:
    return AcademicAdvisoryAgent(agent_id="AGENT_AAA_TEST")


@pytest_asyncio.fixture
async def cs_curriculum(async_session) -> dict[int, list[Course]]:
    """
    Five-semester CS curriculum with two courses per semester so the
    gap-analysis maths is non-trivial.
    """
    courses_by_sem: dict[int, list[Course]] = {}
    for sem in range(1, 6):
        a = Course(
            code=f"CS{sem}01", title=f"CS {sem}01",
            credit_hours=4, semester=sem,
            department="Computer Science",
        )
        b = Course(
            code=f"CS{sem}02", title=f"CS {sem}02",
            credit_hours=3, semester=sem,
            department="Computer Science",
        )
        async_session.add_all([a, b])
        courses_by_sem[sem] = [a, b]
    await async_session.flush()
    return courses_by_sem


# ── Construction & contract ─────────────────────────────────────


def test_agent_extends_course_base_agent_contract():
    a = AcademicAdvisoryAgent()
    assert isinstance(a, CourseBaseAgent)
    assert a.get_status() == AgentStatus.IDLE
    assert a.agent_id.startswith("AGENT_AAA_")


def test_agent_thresholds_match_sds_warning_distinction_floors():
    a = AcademicAdvisoryAgent()
    assert a.HIGH_RISK_CGPA_BELOW == 2.0
    assert a.MEDIUM_RISK_CGPA_BELOW == 2.75
    assert a.HIGH_LOAD_THRESHOLD_ECTS == 18


# ── flag_risk_level ─────────────────────────────────────────────


def test_flag_risk_LOW_for_strong_student_light_load(advisory_agent):
    assert advisory_agent.flag_risk_level(3.6, 12) == RiskStatus.LOW


def test_flag_risk_MEDIUM_for_borderline_cgpa(advisory_agent):
    assert advisory_agent.flag_risk_level(2.5, 12) == RiskStatus.MEDIUM


def test_flag_risk_MEDIUM_for_heavy_load_strong_student(advisory_agent):
    assert advisory_agent.flag_risk_level(3.5, 22) == RiskStatus.MEDIUM


def test_flag_risk_HIGH_for_warning_cgpa(advisory_agent):
    assert advisory_agent.flag_risk_level(1.8, 12) == RiskStatus.HIGH


def test_flag_risk_HIGH_for_borderline_cgpa_with_heavy_load(advisory_agent):
    """CGPA below 2.75 PLUS heavy load escalates to HIGH."""
    assert advisory_agent.flag_risk_level(2.4, 20) == RiskStatus.HIGH


def test_flag_risk_thresholds_overridable_via_constructor():
    strict = AcademicAdvisoryAgent(
        agent_id="AGENT_AAA_STRICT",
        high_risk_cgpa=2.5,
        medium_risk_cgpa=3.2,
    )
    assert strict.flag_risk_level(2.4, 12) == RiskStatus.HIGH
    assert strict.flag_risk_level(3.0, 12) == RiskStatus.MEDIUM


# ── evaluate_study_plan ─────────────────────────────────────────


async def test_evaluate_study_plan_returns_full_gap_when_nothing_completed(
    async_session, advisory_agent, cs_curriculum,
):
    gap = await advisory_agent.evaluate_study_plan(
        async_session,
        department="Computer Science",
        current_semester=3,
        completed_course_ids=set(),
    )
    assert isinstance(gap, GapAnalysis)
    assert gap.completed_count == 0
    # 3 semesters * 2 courses each = 6 required so far
    assert gap.curriculum_size == 6
    assert gap.remaining_count == 6
    assert "CS101" in gap.remaining_required_course_codes


async def test_evaluate_study_plan_credits_completed_courses(
    async_session, advisory_agent, cs_curriculum,
):
    completed_ids = {c.id for c in cs_curriculum[1] + cs_curriculum[2]}
    gap = await advisory_agent.evaluate_study_plan(
        async_session,
        department="Computer Science",
        current_semester=3,
        completed_course_ids=completed_ids,
    )
    assert gap.completed_count == 4
    assert gap.remaining_count == 2
    # The remaining required courses should be the 3rd-semester ones
    assert {"CS301", "CS302"} == set(gap.remaining_required_course_codes)


async def test_evaluate_study_plan_excludes_other_departments(
    async_session, advisory_agent, cs_curriculum,
):
    # Add a noise course in another department
    noise = Course(
        code="MATH101", title="Calc I", credit_hours=4, semester=1,
        department="Mathematics",
    )
    async_session.add(noise)
    await async_session.flush()

    gap = await advisory_agent.evaluate_study_plan(
        async_session,
        department="Computer Science",
        current_semester=2,
        completed_course_ids=set(),
    )
    # 2 semesters * 2 = 4 — MATH101 is excluded
    assert gap.curriculum_size == 4
    assert "MATH101" not in gap.remaining_required_course_codes


# ── provide_academic_guidance ───────────────────────────────────


async def test_recommendations_omit_already_completed_courses(
    async_session, advisory_agent, cs_curriculum,
):
    completed_ids = {cs_curriculum[1][0].id}    # CS101 done
    recs = await advisory_agent.provide_academic_guidance(
        async_session,
        department="Computer Science",
        current_semester=2,
        completed_course_ids=completed_ids,
    )
    codes = [r["code"] for r in recs]
    assert "CS101" not in codes
    assert "CS102" in codes
    assert "CS201" in codes


async def test_recommendations_ordered_by_semester_then_code(
    async_session, advisory_agent, cs_curriculum,
):
    recs = await advisory_agent.provide_academic_guidance(
        async_session,
        department="Computer Science",
        current_semester=3,
        completed_course_ids=set(),
    )
    semesters = [r["semester"] for r in recs]
    assert semesters == sorted(semesters)


async def test_recommendations_include_one_semester_ahead(
    async_session, advisory_agent, cs_curriculum,
):
    """A semester-2 student should also see semester-3 courses suggested."""
    recs = await advisory_agent.provide_academic_guidance(
        async_session,
        department="Computer Science",
        current_semester=2,
        completed_course_ids=set(),
    )
    semesters = {r["semester"] for r in recs}
    assert semesters == {1, 2, 3}


# ── approve_course_load ─────────────────────────────────────────


def test_approve_course_load_blocks_HIGH_risk(advisory_agent):
    assert advisory_agent.approve_course_load(RiskStatus.HIGH) is False


def test_approve_course_load_passes_medium_and_low(advisory_agent):
    assert advisory_agent.approve_course_load(RiskStatus.MEDIUM) is True
    assert advisory_agent.approve_course_load(RiskStatus.LOW) is True


# ── process_task aggregate ──────────────────────────────────────


async def test_process_task_returns_LOW_advice_for_strong_student(
    async_session, advisory_agent, cs_curriculum,
):
    advice = await advisory_agent.process_task({
        "session": async_session,
        "department": "Computer Science",
        "current_semester": 2,
        "cgpa": 3.6,
        "total_proposed_credits": 12,
        "completed_course_ids": {cs_curriculum[1][0].id, cs_curriculum[1][1].id},
    })
    assert isinstance(advice, Advice)
    assert advice.risk_status == RiskStatus.LOW
    assert advice.requires_officer_review is False
    assert advice.gap_analysis.completed_count == 2
    assert "LOW" in advice.explanation


async def test_process_task_flags_HIGH_for_warning_cgpa(
    async_session, advisory_agent, cs_curriculum,
):
    advice = await advisory_agent.process_task({
        "session": async_session,
        "department": "Computer Science",
        "current_semester": 2,
        "cgpa": 1.7,
        "total_proposed_credits": 12,
        "completed_course_ids": set(),
    })
    assert advice.risk_status == RiskStatus.HIGH
    assert advice.requires_officer_review is True
    assert "officer review" in advice.explanation.lower()


async def test_process_task_flags_HIGH_for_borderline_cgpa_heavy_load(
    async_session, advisory_agent, cs_curriculum,
):
    advice = await advisory_agent.process_task({
        "session": async_session,
        "department": "Computer Science",
        "current_semester": 3,
        "cgpa": 2.4,
        "total_proposed_credits": 22,
        "completed_course_ids": set(),
    })
    assert advice.risk_status == RiskStatus.HIGH
    assert advice.requires_officer_review is True


async def test_process_task_serialises_to_dict(
    async_session, advisory_agent, cs_curriculum,
):
    advice = await advisory_agent.process_task({
        "session": async_session,
        "department": "Computer Science",
        "current_semester": 1,
        "cgpa": 3.0,
        "total_proposed_credits": 12,
        "completed_course_ids": set(),
    })
    payload = advice.to_dict()
    assert payload["risk_status"] in {"LOW", "MEDIUM", "HIGH"}
    assert isinstance(payload["recommended_courses"], list)
    assert "completed_count" in payload["gap_analysis"]
    assert "requires_officer_review" in payload
