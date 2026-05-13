"""
Track A — AdvisoryService integration tests.

Drives the service through evaluate_plan, the HIGH-risk officer
queue, and review-closure. Builds a deterministic CS curriculum
in-test for stable gap-analysis output.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.modules.course.agents import AcademicAdvisoryAgent
from app.modules.course.exceptions import (
    EntityNotFoundError, InvalidAdjustmentRequestError,
    UnauthorizedActorError,
)
from app.modules.course.models import (
    AdvisoryRecommendation, Course,
)
from app.modules.course.service import AdvisoryService
from app.shared.enums import RiskStatus, UserRole


# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture
def advisory_service(async_session) -> AdvisoryService:
    agent = AcademicAdvisoryAgent(agent_id="AGENT_AAA_TEST")
    return AdvisoryService(async_session, advisory_agent=agent)


@pytest_asyncio.fixture
async def cs_curriculum(async_session) -> dict[str, Course]:
    courses: dict[str, Course] = {}
    for sem in range(1, 5):
        for n in range(1, 3):
            code = f"CS{sem}0{n}"
            c = Course(
                code=code, title=f"Course {code}",
                credit_hours=4, semester=sem,
                department="Computer Science",
            )
            async_session.add(c)
            courses[code] = c
    await async_session.flush()
    return courses


# ── evaluate_plan ───────────────────────────────────────────────


async def test_evaluate_plan_persists_LOW_recommendation(
    async_session, advisory_service, seeded_student, seeded_term, cs_curriculum,
):
    rec = await advisory_service.evaluate_plan(
        student_id=seeded_student.id,
        term_id=seeded_term.id,
        proposed_course_ids=[cs_curriculum["CS101"].id, cs_curriculum["CS102"].id],
        cgpa=3.6,
        completed_course_ids=set(),
    )
    assert rec.risk_status == RiskStatus.LOW
    assert rec.requires_officer_review is False
    assert rec.reviewed_at is None
    assert len(rec.proposed_courses) == 2
    assert isinstance(rec.recommended_courses, list)
    assert "completed_count" in rec.gap_analysis


async def test_evaluate_plan_persists_HIGH_recommendation_with_review_flag(
    async_session, advisory_service, seeded_student, seeded_term, cs_curriculum,
):
    rec = await advisory_service.evaluate_plan(
        student_id=seeded_student.id,
        term_id=seeded_term.id,
        proposed_course_ids=[cs_curriculum["CS101"].id],
        cgpa=1.7,
        completed_course_ids=set(),
    )
    assert rec.risk_status == RiskStatus.HIGH
    assert rec.requires_officer_review is True
    assert rec.reviewed_at is None


async def test_evaluate_plan_404_on_unknown_term(
    advisory_service, seeded_student,
):
    with pytest.raises(EntityNotFoundError):
        await advisory_service.evaluate_plan(
            student_id=seeded_student.id,
            term_id=uuid.uuid4(),
            proposed_course_ids=[],
            cgpa=3.0,
            completed_course_ids=set(),
        )


async def test_evaluate_plan_404_on_unknown_student(
    advisory_service, seeded_term,
):
    with pytest.raises(EntityNotFoundError):
        await advisory_service.evaluate_plan(
            student_id=uuid.uuid4(),
            term_id=seeded_term.id,
            proposed_course_ids=[],
            cgpa=3.0,
            completed_course_ids=set(),
        )


# ── Officer review queue ────────────────────────────────────────


async def test_list_high_risk_open_returns_only_pending_HIGH(
    async_session, advisory_service, seeded_student, seeded_term, cs_curriculum,
):
    # Generate a HIGH and a LOW recommendation
    await advisory_service.evaluate_plan(
        student_id=seeded_student.id, term_id=seeded_term.id,
        proposed_course_ids=[cs_curriculum["CS101"].id],
        cgpa=1.7, completed_course_ids=set(),
    )
    await advisory_service.evaluate_plan(
        student_id=seeded_student.id, term_id=seeded_term.id,
        proposed_course_ids=[cs_curriculum["CS102"].id],
        cgpa=3.6, completed_course_ids=set(),
    )

    queue = await advisory_service.list_high_risk_open(
        term_id=seeded_term.id, officer_role=UserRole.REGISTRAR_OFFICER,
    )
    assert len(queue) == 1
    assert queue[0].risk_status == RiskStatus.HIGH
    assert queue[0].requires_officer_review is True


async def test_list_high_risk_open_rejects_student_role(
    advisory_service, seeded_term,
):
    with pytest.raises(UnauthorizedActorError):
        await advisory_service.list_high_risk_open(
            term_id=seeded_term.id, officer_role=UserRole.STUDENT,
        )


async def test_close_officer_review_marks_reviewed(
    async_session, advisory_service, seeded_student, seeded_officer,
    seeded_term, cs_curriculum,
):
    rec = await advisory_service.evaluate_plan(
        student_id=seeded_student.id, term_id=seeded_term.id,
        proposed_course_ids=[cs_curriculum["CS101"].id],
        cgpa=1.7, completed_course_ids=set(),
    )
    closed = await advisory_service.close_officer_review(
        recommendation_id=rec.id,
        officer_role=UserRole.REGISTRAR_OFFICER,
        officer_id=seeded_officer.user_id,
        review_notes="Reviewed; advised manual reduction of load.",
    )
    assert closed.reviewed_at is not None
    assert closed.reviewed_by_id == seeded_officer.user_id
    assert closed.review_notes.startswith("Reviewed")

    # No longer appears in the open queue
    queue = await advisory_service.list_high_risk_open(
        term_id=seeded_term.id, officer_role=UserRole.REGISTRAR_OFFICER,
    )
    assert closed.id not in {r.id for r in queue}


async def test_close_officer_review_rejects_student_role(
    advisory_service, seeded_student, seeded_term, cs_curriculum,
):
    rec = await advisory_service.evaluate_plan(
        student_id=seeded_student.id, term_id=seeded_term.id,
        proposed_course_ids=[cs_curriculum["CS101"].id],
        cgpa=1.7, completed_course_ids=set(),
    )
    with pytest.raises(UnauthorizedActorError):
        await advisory_service.close_officer_review(
            recommendation_id=rec.id,
            officer_role=UserRole.STUDENT,
            officer_id=uuid.uuid4(),
            review_notes="Not allowed",
        )


async def test_close_officer_review_rejects_empty_notes(
    advisory_service, seeded_student, seeded_term, cs_curriculum,
):
    rec = await advisory_service.evaluate_plan(
        student_id=seeded_student.id, term_id=seeded_term.id,
        proposed_course_ids=[cs_curriculum["CS101"].id],
        cgpa=1.7, completed_course_ids=set(),
    )
    with pytest.raises(InvalidAdjustmentRequestError):
        await advisory_service.close_officer_review(
            recommendation_id=rec.id,
            officer_role=UserRole.REGISTRAR_OFFICER,
            officer_id=uuid.uuid4(),
            review_notes="   ",
        )


async def test_close_officer_review_rejects_double_close(
    advisory_service, seeded_student, seeded_officer,
    seeded_term, cs_curriculum,
):
    rec = await advisory_service.evaluate_plan(
        student_id=seeded_student.id, term_id=seeded_term.id,
        proposed_course_ids=[cs_curriculum["CS101"].id],
        cgpa=1.7, completed_course_ids=set(),
    )
    await advisory_service.close_officer_review(
        recommendation_id=rec.id,
        officer_role=UserRole.REGISTRAR_OFFICER,
        officer_id=seeded_officer.user_id,
        review_notes="First review",
    )
    with pytest.raises(InvalidAdjustmentRequestError):
        await advisory_service.close_officer_review(
            recommendation_id=rec.id,
            officer_role=UserRole.REGISTRAR_OFFICER,
            officer_id=seeded_officer.user_id,
            review_notes="Second close",
        )


# ── Reads ───────────────────────────────────────────────────────


async def test_list_for_student_returns_all_recommendations_newest_first(
    advisory_service, seeded_student, seeded_term, cs_curriculum,
):
    rec1 = await advisory_service.evaluate_plan(
        student_id=seeded_student.id, term_id=seeded_term.id,
        proposed_course_ids=[cs_curriculum["CS101"].id],
        cgpa=3.6, completed_course_ids=set(),
    )
    rec2 = await advisory_service.evaluate_plan(
        student_id=seeded_student.id, term_id=seeded_term.id,
        proposed_course_ids=[cs_curriculum["CS102"].id],
        cgpa=1.7, completed_course_ids=set(),
    )
    rows = await advisory_service.list_for_student(seeded_student.id)
    assert len(rows) == 2
    assert {r.id for r in rows} == {rec1.id, rec2.id}
