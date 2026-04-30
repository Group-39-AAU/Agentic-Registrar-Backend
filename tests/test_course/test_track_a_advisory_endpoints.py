"""
Track A — advisory escalation flow integration tests.

Walks the full HIGH-risk pipeline: student requests evaluation,
verdict lands in the officer queue, officer closes the review with
notes, queue empties. Drives via the service layer to stay
HTTP-dependency-free, mirroring the Track A.4 endpoint test
convention.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.modules.course.agents import AcademicAdvisoryAgent
from app.modules.course.models import (
    AdvisoryRecommendation, Course,
)
from app.modules.course.service import AdvisoryService
from app.shared.enums import RiskStatus, UserRole


# ── Fixtures ────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def advisory_service(async_session) -> AdvisoryService:
    agent = AcademicAdvisoryAgent(agent_id="AGENT_AAA_TEST")
    return AdvisoryService(async_session, advisory_agent=agent)


@pytest_asyncio.fixture
async def cs_courses(async_session) -> list[Course]:
    courses = [
        Course(code="CS101", title="Intro", credit_hours=4, semester=1,
               department="Computer Science"),
        Course(code="CS102", title="Discrete", credit_hours=4, semester=1,
               department="Computer Science"),
        Course(code="CS201", title="Data Structures", credit_hours=4,
               semester=2, department="Computer Science"),
    ]
    async_session.add_all(courses)
    await async_session.flush()
    return courses


# ── Full HIGH-risk escalation lifecycle ─────────────────────────


async def test_high_risk_evaluation_lands_in_officer_queue(
    advisory_service, seeded_student, seeded_term, cs_courses,
):
    rec = await advisory_service.evaluate_plan(
        student_id=seeded_student.id,
        term_id=seeded_term.id,
        proposed_course_ids=[c.id for c in cs_courses],
        cgpa=1.7,           # Warning floor — escalates to HIGH
        completed_course_ids=set(),
    )
    assert rec.risk_status == RiskStatus.HIGH
    assert rec.requires_officer_review is True

    queue = await advisory_service.list_high_risk_open(
        term_id=seeded_term.id, officer_role=UserRole.REGISTRAR_OFFICER,
    )
    assert rec.id in {r.id for r in queue}


async def test_low_risk_evaluation_does_not_appear_in_officer_queue(
    advisory_service, seeded_student, seeded_term, cs_courses,
):
    rec = await advisory_service.evaluate_plan(
        student_id=seeded_student.id,
        term_id=seeded_term.id,
        proposed_course_ids=[cs_courses[0].id, cs_courses[1].id],
        cgpa=3.6,
        completed_course_ids=set(),
    )
    assert rec.risk_status == RiskStatus.LOW
    queue = await advisory_service.list_high_risk_open(
        term_id=seeded_term.id, officer_role=UserRole.REGISTRAR_OFFICER,
    )
    assert rec.id not in {r.id for r in queue}


async def test_officer_close_review_drains_queue_and_records_notes(
    async_session, advisory_service, seeded_student, seeded_term, cs_courses,
):
    rec = await advisory_service.evaluate_plan(
        student_id=seeded_student.id,
        term_id=seeded_term.id,
        proposed_course_ids=[c.id for c in cs_courses],
        cgpa=1.7,
        completed_course_ids=set(),
    )
    queue_before = await advisory_service.list_high_risk_open(
        term_id=seeded_term.id, officer_role=UserRole.REGISTRAR_OFFICER,
    )
    assert len(queue_before) == 1

    closed = await advisory_service.close_officer_review(
        recommendation_id=rec.id,
        officer_role=UserRole.REGISTRAR_OFFICER,
        officer_id=uuid.uuid4(),
        review_notes="Met with student; advised dropping CS201 to lower load.",
    )
    assert closed.reviewed_at is not None
    assert "CS201" in closed.review_notes

    queue_after = await advisory_service.list_high_risk_open(
        term_id=seeded_term.id, officer_role=UserRole.REGISTRAR_OFFICER,
    )
    assert len(queue_after) == 0


async def test_student_can_list_their_own_recommendations(
    advisory_service, seeded_student, seeded_term, cs_courses,
):
    await advisory_service.evaluate_plan(
        student_id=seeded_student.id, term_id=seeded_term.id,
        proposed_course_ids=[cs_courses[0].id], cgpa=3.0,
        completed_course_ids=set(),
    )
    await advisory_service.evaluate_plan(
        student_id=seeded_student.id, term_id=seeded_term.id,
        proposed_course_ids=[cs_courses[1].id, cs_courses[2].id],
        cgpa=1.7, completed_course_ids=set(),
    )
    rows = await advisory_service.list_for_student(seeded_student.id)
    assert len(rows) == 2
    assert {r.risk_status for r in rows} >= {RiskStatus.HIGH}


async def test_recommendation_carries_recommended_courses_payload(
    advisory_service, seeded_student, seeded_term, cs_courses,
):
    """
    The persisted recommended_courses JSON should preserve the
    agent's prioritised list so the portal can render it.
    """
    rec = await advisory_service.evaluate_plan(
        student_id=seeded_student.id,
        term_id=seeded_term.id,
        proposed_course_ids=[cs_courses[0].id],
        cgpa=3.4,
        completed_course_ids=set(),
    )
    assert isinstance(rec.recommended_courses, list)
    assert len(rec.recommended_courses) > 0
    sample = rec.recommended_courses[0]
    assert "code" in sample and "credit_hours" in sample
    assert sample["is_core"] is True
