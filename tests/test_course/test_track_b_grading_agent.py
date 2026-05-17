"""
Track B PR 3 — GradingMonitorAgent end-to-end (mocked LLM).

These exercise the LLM-as-reasoner agent through the submit → review
→ persist → iterate path. The Gemini client is replaced with a
``FakeLLMClient`` whose response is scripted per test so we control
the verdict without an API key or network call.

We also assert that the deterministic ``tool_findings`` payload the
agent compiles is sane (contains the metrics the LLM is supposed to
read) — that's the contract between the tool layer and the LLM.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, time
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.ai.llm_client import LLMUnavailableError
from app.modules.auth.models import User
from app.modules.course.exceptions import GradeBatchNotEditableError
from app.modules.course.grading.agents import GradingMonitorAgent
from app.modules.course.grading.models import (
    AssessmentBreakdown, GradeAgentReview, GradeBatch,
)
from app.modules.course.grading.schemas import (
    AssessmentBreakdownCreate, AssessmentComponentCreate,
    BulkScoreWrite, ScoreCellWrite,
)
from app.modules.course.grading.service import InstructorGradingService
from app.modules.course.models import (
    AcademicTerm, ClassScheduleSlot, Course, Instructor,
    Registration, RegistrationCourse, Section, Student,
)
from app.shared.enums import (
    AcademicPhase, EnrollmentStatus, GradeSubmissionStatus,
    RegistrationStatus, SponsorshipType, UserRole,
)


# ── Fake LLM client ─────────────────────────────────────────────


class FakeLLMClient:
    """
    Scripted stand-in for :class:`LLMClient`. The agent only calls
    ``review_grade_batch_as_dh`` on it, so that's all we need to
    fake. Tests pass in either a response dict (verdict path) or an
    exception (failure path).
    """

    def __init__(self, *, response=None, raise_exc=None):
        self.response = response
        self.raise_exc = raise_exc
        self.calls: list[dict] = []

    async def review_grade_batch_as_dh(
        self, review_payload: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append(review_payload)
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.response


# ── Scenario fixture ────────────────────────────────────────────


@pytest_asyncio.fixture
async def grading_scenario(async_session):
    """A 4-student CS101 cohort with the instructor wired up."""
    term = AcademicTerm(
        term_name="PR3-Test-2026",
        phase=AcademicPhase.ONE,
        start_date=date(2026, 9, 1),
        end_date=date(2027, 1, 31),
        is_open=True,
    )
    course = Course(
        code="CS101", title="Intro Programming",
        credit_hours=3, semester=1, department="Computer Science",
    )
    async_session.add_all([term, course])
    await async_session.flush()

    instr_user = User(
        id=uuid.uuid4(),
        email="lemma-pr3@aau.edu.et",
        first_name="Lemma", last_name="Bekele",
        hashed_password="x", role=UserRole.INSTRUCTOR, is_active=True,
    )
    async_session.add(instr_user)
    await async_session.flush()

    instructor = Instructor(
        user_id=instr_user.id,
        instructor_id="STAFF/PR3/15",
        department="Computer Science",
    )
    async_session.add(instructor)
    await async_session.flush()

    section = Section(
        term_id=term.id, department="Computer Science",
        semester=1, section_code="A", capacity=30, enrolled_count=0,
    )
    async_session.add(section)
    await async_session.flush()

    async_session.add(ClassScheduleSlot(
        section_id=section.id, course_id=course.id,
        instructor_id=instructor.id,
        day_of_week="MON", start_time=time(9, 0), end_time=time(10, 0),
        room="LAB-1",
    ))
    await async_session.flush()

    students = []
    for sid, name in [
        ("UGR/0001/15", "Abel Tesfaye"),
        ("UGR/0002/15", "Bethel Demissie"),
        ("UGR/0003/15", "Chala Worku"),
        ("UGR/0004/15", "Dawit Asefa"),
    ]:
        user = User(
            id=uuid.uuid4(),
            email=f"{sid.lower().replace('/', '-')}-pr3@aau.edu.et",
            first_name=name.split()[0], last_name=name.split()[-1],
            hashed_password="x", role=UserRole.STUDENT, is_active=True,
        )
        async_session.add(user)
        await async_session.flush()
        s = Student(
            user_id=user.id, student_id=sid, full_name=name,
            current_semester=1, department="Computer Science",
            sponsorship_type=SponsorshipType.GOVERNMENT,
            enrollment_status=EnrollmentStatus.ACTIVE,
        )
        async_session.add(s)
        await async_session.flush()
        reg = Registration(
            student_id=s.id, term_id=term.id, section_id=section.id,
            status=RegistrationStatus.REGISTERED,
            sponsorship_type=SponsorshipType.GOVERNMENT,
        )
        async_session.add(reg)
        await async_session.flush()
        async_session.add(RegistrationCourse(
            registration_id=reg.id, course_id=course.id, is_dropped=False,
        ))
        await async_session.flush()
        students.append(s)

    return {
        "term": term, "course": course,
        "instructor": instructor, "instr_user": instr_user,
        "section": section, "students": students,
    }


def _three_one_breakdown() -> AssessmentBreakdownCreate:
    return AssessmentBreakdownCreate(components=[
        AssessmentComponentCreate(name="Quiz",  weight=30, max_score=10),
        AssessmentComponentCreate(name="Mid",   weight=30, max_score=50),
        AssessmentComponentCreate(name="Final", weight=40, max_score=100),
    ])


async def _build_full_batch(
    svc: InstructorGradingService,
    w: dict,
    *,
    score_grid: list[tuple[int, int, int]],
):
    """Helper: post breakdown + full scores using `score_grid`."""
    bd = await svc.upsert_breakdown(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
        payload=_three_one_breakdown(),
    )
    batch = await svc.get_or_create_batch(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
    )
    quiz_id  = next(c.id for c in bd.components if c.name == "Quiz")
    mid_id   = next(c.id for c in bd.components if c.name == "Mid")
    final_id = next(c.id for c in bd.components if c.name == "Final")
    cells = []
    for student, (q, m, f) in zip(w["students"], score_grid):
        cells.append(ScoreCellWrite(student_id=student.id, component_id=quiz_id,  score=q))
        cells.append(ScoreCellWrite(student_id=student.id, component_id=mid_id,   score=m))
        cells.append(ScoreCellWrite(student_id=student.id, component_id=final_id, score=f))
    await svc.upsert_scores(
        user_id=w["instr_user"].id, batch_id=batch.id,
        payload=BulkScoreWrite(cells=cells),
    )
    return bd, batch


# ── End-to-end happy path (APPROVE) ─────────────────────────────


async def test_submit_with_llm_approve_transitions_to_submitted(
    async_session, grading_scenario,
):
    w = grading_scenario
    fake = FakeLLMClient(response={
        "verdict": "APPROVE",
        "flags": [],
        "reasoning": "Distribution is healthy; pass rate matches historical baseline.",
    })
    agent = GradingMonitorAgent(llm_client=fake)
    svc = InstructorGradingService(async_session, grading_agent=agent)

    _bd, batch = await _build_full_batch(
        svc, w, score_grid=[(10, 50, 100), (9, 45, 90), (8, 40, 80), (7, 35, 70)],
    )
    result = await svc.submit_batch(
        user_id=w["instr_user"].id, batch_id=batch.id,
    )
    assert result.agent_verdict == "APPROVE"
    assert result.status is GradeSubmissionStatus.SUBMITTED
    assert "healthy" in result.agent_reasoning

    # LLM was called exactly once with a context dict including the
    # statistical signals the agent is supposed to surface.
    assert len(fake.calls) == 1
    ctx = fake.calls[0]
    assert ctx["class_stats"]["n"] == 4
    assert ctx["class_stats"]["mean"] is not None
    assert "distribution" in ctx
    assert "components" in ctx
    assert ctx["breakdown"]["sums_to_100"] is True
    assert ctx["iteration"] == 1
    assert ctx["instructor_justification"] is None

    # A GradeAgentReview row was persisted with the LLM's verdict
    # and the deterministic tool_findings.
    reviews = (await async_session.execute(
        select(GradeAgentReview).where(GradeAgentReview.batch_id == batch.id)
    )).scalars().all()
    assert len(reviews) == 1
    assert reviews[0].verdict == "APPROVE"
    assert reviews[0].tool_findings["class_stats"]["n"] == 4
    assert reviews[0].llm_reasoning is not None


# ── FLAG path ───────────────────────────────────────────────────


async def test_submit_with_llm_flag_transitions_to_flagged(
    async_session, grading_scenario,
):
    w = grading_scenario
    fake = FakeLLMClient(response={
        "verdict": "FLAG",
        "flags": [
            {
                "type": "MASS_FAILURE",
                "severity": "HIGH",
                "message": "100% fail rate — this looks unusual.",
                "affected_students": ["UGR/0001/15", "UGR/0002/15"],
            },
        ],
        "reasoning": "Every student failed. Please review your grading rubric.",
    })
    agent = GradingMonitorAgent(llm_client=fake)
    svc = InstructorGradingService(async_session, grading_agent=agent)

    # Everyone fails — gives the LLM material to flag.
    _bd, batch = await _build_full_batch(
        svc, w, score_grid=[(2, 10, 30), (3, 12, 28), (1, 8, 25), (2, 9, 27)],
    )
    result = await svc.submit_batch(
        user_id=w["instr_user"].id, batch_id=batch.id,
    )
    assert result.agent_verdict == "FLAG"
    assert result.status is GradeSubmissionStatus.FLAGGED
    assert len(result.agent_flags) == 1
    assert result.agent_flags[0]["type"] == "MASS_FAILURE"


# ── PENDING path (LLM failure) ──────────────────────────────────


async def test_submit_with_llm_failure_yields_pending(
    async_session, grading_scenario,
):
    """LLM down → verdict PENDING, batch stays SUBMITTED (not stuck)."""
    w = grading_scenario
    fake = FakeLLMClient(raise_exc=LLMUnavailableError("Gemini timeout"))
    agent = GradingMonitorAgent(llm_client=fake)
    svc = InstructorGradingService(async_session, grading_agent=agent)

    _bd, batch = await _build_full_batch(
        svc, w, score_grid=[(10, 50, 100), (9, 45, 90), (8, 40, 80), (7, 35, 70)],
    )
    result = await svc.submit_batch(
        user_id=w["instr_user"].id, batch_id=batch.id,
    )
    assert result.agent_verdict == "PENDING"
    # Batch is SUBMITTED — not deadlocked, DH workflow can re-trigger.
    assert result.status is GradeSubmissionStatus.SUBMITTED
    # The review row still landed, even without an LLM verdict —
    # tool findings are preserved so the DH can see them.
    reviews = (await async_session.execute(
        select(GradeAgentReview).where(GradeAgentReview.batch_id == batch.id)
    )).scalars().all()
    assert len(reviews) == 1
    assert reviews[0].verdict == "PENDING"
    assert reviews[0].llm_reasoning is None
    assert reviews[0].tool_findings["class_stats"]["n"] == 4


async def test_submit_with_no_llm_client_yields_pending(
    async_session, grading_scenario,
):
    """No LLM client at all → PENDING, same graceful degrade."""
    w = grading_scenario
    agent = GradingMonitorAgent(llm_client=None)
    svc = InstructorGradingService(async_session, grading_agent=agent)

    _bd, batch = await _build_full_batch(
        svc, w, score_grid=[(10, 50, 100), (9, 45, 90), (8, 40, 80), (7, 35, 70)],
    )
    result = await svc.submit_batch(
        user_id=w["instr_user"].id, batch_id=batch.id,
    )
    assert result.agent_verdict == "PENDING"
    assert result.status is GradeSubmissionStatus.SUBMITTED


async def test_submit_with_malformed_llm_output_yields_pending(
    async_session, grading_scenario,
):
    """LLM returns gibberish verdict → PENDING (safety net)."""
    w = grading_scenario
    fake = FakeLLMClient(response={
        "verdict": "MAYBE", "flags": [], "reasoning": "Unsure",
    })
    agent = GradingMonitorAgent(llm_client=fake)
    svc = InstructorGradingService(async_session, grading_agent=agent)

    _bd, batch = await _build_full_batch(
        svc, w, score_grid=[(10, 50, 100), (9, 45, 90), (8, 40, 80), (7, 35, 70)],
    )
    result = await svc.submit_batch(
        user_id=w["instr_user"].id, batch_id=batch.id,
    )
    assert result.agent_verdict == "PENDING"


# ── Iteration loop: justify → APPROVE on retry ──────────────────


async def test_justify_reruns_agent_and_can_approve(
    async_session, grading_scenario,
):
    """
    Iteration 1: FLAG. Iteration 2: justified, LLM accepts → APPROVE.
    Each run appends a new review row and bumps iteration_count.
    """
    w = grading_scenario

    # Set up a script: iteration 1 = FLAG, iteration 2 = APPROVE.
    class ScriptedLLM:
        def __init__(self):
            self.iteration = 0
            self.calls = []

        async def review_grade_batch_as_dh(self, review_payload):
            self.iteration += 1
            self.calls.append(review_payload)
            if self.iteration == 1:
                return {
                    "verdict": "FLAG",
                    "flags": [{
                        "type": "MASS_FAILURE",
                        "severity": "HIGH",
                        "message": "All four students failed.",
                    }],
                    "reasoning": "Pass rate is 0%. Please justify or correct.",
                }
            return {
                "verdict": "APPROVE",
                "flags": [],
                "reasoning": "Justification accepted — exam was a final remedial.",
            }

    script = ScriptedLLM()
    agent = GradingMonitorAgent(llm_client=script)
    svc = InstructorGradingService(async_session, grading_agent=agent)

    _bd, batch = await _build_full_batch(
        svc, w, score_grid=[(2, 10, 30), (3, 12, 28), (1, 8, 25), (2, 9, 27)],
    )
    first = await svc.submit_batch(
        user_id=w["instr_user"].id, batch_id=batch.id,
    )
    assert first.agent_verdict == "FLAG"
    assert first.status is GradeSubmissionStatus.FLAGGED

    second = await svc.submit_justification(
        user_id=w["instr_user"].id,
        batch_id=batch.id,
        justification=(
            "This was the final remedial exam. Students who failed are "
            "required to retake; this distribution reflects the policy."
        ),
    )
    assert second.agent_verdict == "APPROVE"
    assert second.status is GradeSubmissionStatus.SUBMITTED
    assert second.iteration == 2

    # The justification was included in the iteration-2 LLM context.
    assert script.calls[1]["instructor_justification"] is not None
    assert "remedial" in script.calls[1]["instructor_justification"].lower()
    assert script.calls[1]["iteration"] == 2

    # Two review rows now exist, most-recent first.
    rows = await svc.list_agent_reviews(
        user_id=w["instr_user"].id, batch_id=batch.id,
    )
    assert len(rows) == 2
    assert rows[0].verdict == "APPROVE"  # most recent
    assert rows[0].iteration == 2
    assert rows[1].verdict == "FLAG"
    assert rows[1].iteration == 1


async def test_justify_rejected_when_not_flagged(
    async_session, grading_scenario,
):
    """Can only justify a FLAGGED batch — DRAFT is 409."""
    w = grading_scenario
    fake = FakeLLMClient(response={"verdict": "APPROVE", "flags": [], "reasoning": "fine"})
    agent = GradingMonitorAgent(llm_client=fake)
    svc = InstructorGradingService(async_session, grading_agent=agent)

    _bd, batch = await _build_full_batch(
        svc, w, score_grid=[(10, 50, 100), (9, 45, 90), (8, 40, 80), (7, 35, 70)],
    )
    # Don't submit — batch stays DRAFT.
    with pytest.raises(GradeBatchNotEditableError):
        await svc.submit_justification(
            user_id=w["instr_user"].id, batch_id=batch.id,
            justification="x" * 20,
        )


# ── Reopen path ─────────────────────────────────────────────────


async def test_reopen_flagged_back_to_draft(async_session, grading_scenario):
    """Reopen: FLAGGED → DRAFT, iteration bumped, scores preserved."""
    w = grading_scenario
    fake = FakeLLMClient(response={
        "verdict": "FLAG",
        "flags": [{"type": "GRADE_INFLATION", "severity": "MEDIUM", "message": "x"}],
        "reasoning": "x",
    })
    agent = GradingMonitorAgent(llm_client=fake)
    svc = InstructorGradingService(async_session, grading_agent=agent)

    _bd, batch = await _build_full_batch(
        svc, w, score_grid=[(10, 50, 100), (10, 50, 100), (10, 50, 100), (10, 50, 100)],
    )
    first = await svc.submit_batch(
        user_id=w["instr_user"].id, batch_id=batch.id,
    )
    assert first.status is GradeSubmissionStatus.FLAGGED

    reopened = await svc.reopen_batch(
        user_id=w["instr_user"].id, batch_id=batch.id,
    )
    assert reopened.status is GradeSubmissionStatus.DRAFT
    assert reopened.iteration_count == 2
    # Scores survived the reopen (only status changed).
    assert all(
        all(cell.score is not None for cell in row.scores)
        for row in reopened.rows
    )


async def test_reopen_rejected_when_not_flagged(
    async_session, grading_scenario,
):
    w = grading_scenario
    fake = FakeLLMClient(response={"verdict": "APPROVE", "flags": [], "reasoning": "ok"})
    agent = GradingMonitorAgent(llm_client=fake)
    svc = InstructorGradingService(async_session, grading_agent=agent)
    _bd, batch = await _build_full_batch(
        svc, w, score_grid=[(10, 50, 100), (9, 45, 90), (8, 40, 80), (7, 35, 70)],
    )
    # DRAFT — can't reopen what isn't flagged.
    with pytest.raises(GradeBatchNotEditableError):
        await svc.reopen_batch(
            user_id=w["instr_user"].id, batch_id=batch.id,
        )


# ── Agent context: per-component stats land in the LLM payload ─


async def test_llm_context_includes_per_component_stats(
    async_session, grading_scenario,
):
    """The LLM must see per-component breakdown — that's the whole point."""
    w = grading_scenario
    fake = FakeLLMClient(response={"verdict": "APPROVE", "flags": [], "reasoning": "ok"})
    agent = GradingMonitorAgent(llm_client=fake)
    svc = InstructorGradingService(async_session, grading_agent=agent)

    _bd, batch = await _build_full_batch(
        svc, w, score_grid=[(10, 30, 60), (9, 35, 65), (8, 40, 70), (7, 45, 75)],
    )
    await svc.submit_batch(user_id=w["instr_user"].id, batch_id=batch.id)

    ctx = fake.calls[0]
    component_names = {c["name"] for c in ctx["components"]}
    assert component_names == {"Quiz", "Mid", "Final"}
    # Mid mean: (30+35+40+45)/4 = 37.5; max_score=50 → 75%.
    mid = next(c for c in ctx["components"] if c["name"] == "Mid")
    assert mid["mean_pct"] == 75.0
