"""
Track B PR 4 — Department-head authorisation workflow.

Exercises every terminal-decision path the DH can take on a grade
batch:

  SUBMITTED + APPROVE  → AUTHORISED  (decision AUTHORISED, justification optional)
  SUBMITTED + APPROVE  → REJECTED    (decision OVERRODE_AGENT_APPROVAL, justification required)
  FLAGGED              → AUTHORISED  (decision OVERRODE_AGENT_FLAG, justification required)
  FLAGGED              → REJECTED    (decision REJECTED, justification required)

Plus:
  - Role gate: non-DH users get 403.
  - Justification required for the three "needs reason" decisions.
  - REJECTED + reopen path so the instructor can iterate.
  - Grade rows reflect the new lifecycle (AUTHORISED / REJECTED).
  - rerun-agent path for PENDING verdicts.
  - Append-only decision history.
"""
from __future__ import annotations

import uuid
from datetime import date, time

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.modules.auth.models import User
from app.modules.course.exceptions import (
    DepartmentHeadRoleRequiredError, GradeBatchNotReviewableError,
    JustificationRequiredError,
)
from app.modules.course.grading.agents import GradingMonitorAgent
from app.modules.course.grading.dh_service import (
    DepartmentHeadGradingService,
)
from app.modules.course.grading.models import (
    GradeAgentReview, GradeAuthorisationDecision, GradeBatch,
)
from app.modules.course.grading.schemas import (
    AssessmentBreakdownCreate, AssessmentComponentCreate,
    BulkScoreWrite, ScoreCellWrite,
)
from app.modules.course.grading.service import InstructorGradingService
from app.modules.course.models import (
    AcademicTerm, ClassScheduleSlot, Course, CourseManagementOfficer,
    Grade, Instructor, Registration, RegistrationCourse, Section, Student,
)
from app.shared.enums import (
    AcademicPhase, EnrollmentStatus, GradeSubmissionStatus, OfficerRole,
    RegistrationStatus, SponsorshipType, UserRole,
)


class _ApproveLLM:
    async def review_grade_batch_as_dh(self, review_payload):
        return {"verdict": "APPROVE", "flags": [], "reasoning": "fine"}


class _FlagLLM:
    async def review_grade_batch_as_dh(self, review_payload):
        return {
            "verdict": "FLAG",
            "flags": [{"type": "MASS_FAILURE", "severity": "HIGH", "message": "fail"}],
            "reasoning": "Everyone failed.",
        }


def _approve_agent() -> GradingMonitorAgent:
    return GradingMonitorAgent(llm_client=_ApproveLLM())


def _flag_agent() -> GradingMonitorAgent:
    return GradingMonitorAgent(llm_client=_FlagLLM())


# ── Scenario fixture (instructor + DH + 3 students) ─────────────


@pytest_asyncio.fixture
async def dh_scenario(async_session):
    term = AcademicTerm(
        term_name="PR4-Test-2026",
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
        email="lemma-pr4@aau.edu.et",
        first_name="Lemma", last_name="Bekele",
        hashed_password="x", role=UserRole.INSTRUCTOR, is_active=True,
    )
    dh_user = User(
        id=uuid.uuid4(),
        email="dh-pr4@aau.edu.et",
        first_name="Almaz", last_name="Tilahun",
        hashed_password="x", role=UserRole.REGISTRAR_OFFICER, is_active=True,
    )
    other_user = User(
        id=uuid.uuid4(),
        email="other-pr4@aau.edu.et",
        first_name="Other", last_name="Officer",
        hashed_password="x", role=UserRole.REGISTRAR_OFFICER, is_active=True,
    )
    async_session.add_all([instr_user, dh_user, other_user])
    await async_session.flush()

    instructor = Instructor(
        user_id=instr_user.id,
        instructor_id="STAFF/PR4/15",
        department="Computer Science",
    )
    async_session.add(instructor)
    # DH officer (role=DEPARTMENT_HEAD)
    async_session.add(CourseManagementOfficer(
        user_id=dh_user.id, staff_id="REG/PR4/15",
        role=OfficerRole.DEPARTMENT_HEAD, authorization_level=5,
    ))
    # Plain registrar officer (NOT a department head) — should be denied.
    async_session.add(CourseManagementOfficer(
        user_id=other_user.id, staff_id="REG/OTH/15",
        role=OfficerRole.REGISTRAR_OFFICER, authorization_level=3,
    ))
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
    ]:
        u = User(
            id=uuid.uuid4(),
            email=f"{sid.lower().replace('/', '-')}-pr4@aau.edu.et",
            first_name=name.split()[0], last_name=name.split()[-1],
            hashed_password="x", role=UserRole.STUDENT, is_active=True,
        )
        async_session.add(u)
        await async_session.flush()
        s = Student(
            user_id=u.id, student_id=sid, full_name=name,
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
        "dh_user": dh_user, "other_user": other_user,
        "section": section, "students": students,
    }


async def _build_full_batch(
    svc: InstructorGradingService, w: dict,
    *, score_grid: list[tuple[int, int, int]],
):
    bd = await svc.upsert_breakdown(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
        payload=AssessmentBreakdownCreate(components=[
            AssessmentComponentCreate(name="Quiz",  weight=30, max_score=10),
            AssessmentComponentCreate(name="Mid",   weight=30, max_score=50),
            AssessmentComponentCreate(name="Final", weight=40, max_score=100),
        ]),
    )
    batch = await svc.get_or_create_batch(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
    )
    qid, mid, fid = (
        next(c.id for c in bd.components if c.name == n)
        for n in ("Quiz", "Mid", "Final")
    )
    cells = []
    for student, (q, m, f) in zip(w["students"], score_grid):
        cells.extend([
            ScoreCellWrite(student_id=student.id, component_id=qid, score=q),
            ScoreCellWrite(student_id=student.id, component_id=mid, score=m),
            ScoreCellWrite(student_id=student.id, component_id=fid, score=f),
        ])
    await svc.upsert_scores(
        user_id=w["instr_user"].id, batch_id=batch.id,
        payload=BulkScoreWrite(cells=cells),
    )
    return bd, batch


# ── Role gate ───────────────────────────────────────────────────


async def test_non_dh_denied_403(async_session, dh_scenario):
    w = dh_scenario
    dh = DepartmentHeadGradingService(async_session)
    with pytest.raises(DepartmentHeadRoleRequiredError):
        # Plain REGISTRAR_OFFICER, not DEPARTMENT_HEAD.
        await dh.list_queue(user_id=w["other_user"].id)


async def test_user_without_officer_row_denied(async_session, dh_scenario):
    """An ADMIN user passes; a plain user with no officer row does not."""
    w = dh_scenario
    stranger = User(
        id=uuid.uuid4(), email="stranger-pr4@aau.edu.et",
        first_name="Stranger", last_name="X",
        hashed_password="x", role=UserRole.REGISTRAR_OFFICER, is_active=True,
    )
    async_session.add(stranger)
    await async_session.flush()
    dh = DepartmentHeadGradingService(async_session)
    with pytest.raises(DepartmentHeadRoleRequiredError):
        await dh.list_queue(user_id=stranger.id)


async def test_admin_user_allowed(async_session, dh_scenario):
    """An ADMIN user can act as a DH without needing an officer row."""
    w = dh_scenario
    admin = User(
        id=uuid.uuid4(), email="admin-pr4@aau.edu.et",
        first_name="Admin", last_name="X",
        hashed_password="x", role=UserRole.ADMIN, is_active=True,
    )
    async_session.add(admin)
    await async_session.flush()
    dh = DepartmentHeadGradingService(async_session)
    queue = await dh.list_queue(user_id=admin.id)
    assert isinstance(queue, list)


# ── Queue ───────────────────────────────────────────────────────


async def test_queue_lists_submitted_and_flagged(
    async_session, dh_scenario,
):
    w = dh_scenario
    svc = InstructorGradingService(async_session, grading_agent=_approve_agent())
    _bd, batch = await _build_full_batch(
        svc, w, score_grid=[(10, 50, 100), (9, 45, 90), (8, 40, 80)],
    )
    await svc.submit_batch(user_id=w["instr_user"].id, batch_id=batch.id)

    dh = DepartmentHeadGradingService(async_session)
    queue = await dh.list_queue(user_id=w["dh_user"].id)
    assert len(queue) == 1
    entry = queue[0]
    assert entry.batch_id == batch.id
    assert entry.status is GradeSubmissionStatus.SUBMITTED
    assert entry.latest_agent_verdict == "APPROVE"
    assert entry.flag_count == 0
    assert entry.roster_total == 3
    assert entry.instructor_name == "Lemma Bekele"


async def test_queue_excludes_authorised_and_rejected(
    async_session, dh_scenario,
):
    """Terminal states never show up in the queue."""
    w = dh_scenario
    svc = InstructorGradingService(async_session, grading_agent=_approve_agent())
    _bd, batch = await _build_full_batch(
        svc, w, score_grid=[(10, 50, 100), (9, 45, 90), (8, 40, 80)],
    )
    await svc.submit_batch(user_id=w["instr_user"].id, batch_id=batch.id)

    dh = DepartmentHeadGradingService(async_session)
    await dh.authorise(user_id=w["dh_user"].id, batch_id=batch.id)
    queue = await dh.list_queue(user_id=w["dh_user"].id)
    assert queue == []


# ── Authorise paths ─────────────────────────────────────────────


async def test_authorise_submitted_flips_grades_to_authorised(
    async_session, dh_scenario,
):
    w = dh_scenario
    svc = InstructorGradingService(async_session, grading_agent=_approve_agent())
    _bd, batch = await _build_full_batch(
        svc, w, score_grid=[(10, 50, 100), (9, 45, 90), (8, 40, 80)],
    )
    await svc.submit_batch(user_id=w["instr_user"].id, batch_id=batch.id)

    dh = DepartmentHeadGradingService(async_session)
    result = await dh.authorise(
        user_id=w["dh_user"].id, batch_id=batch.id,
    )
    assert result.new_status is GradeSubmissionStatus.AUTHORISED
    assert result.decision == "AUTHORISED"

    # Batch row flipped.
    refreshed_batch = (await async_session.execute(
        select(GradeBatch).where(GradeBatch.id == batch.id)
    )).scalar_one()
    assert refreshed_batch.status is GradeSubmissionStatus.AUTHORISED

    # Every Grade row flipped to AUTHORISED with the DH stamp.
    grades = (await async_session.execute(
        select(Grade).where(
            Grade.course_id == w["course"].id,
            Grade.term_id == w["term"].id,
        )
    )).scalars().all()
    assert len(grades) == 3
    assert all(g.status is GradeSubmissionStatus.AUTHORISED for g in grades)
    assert all(g.authorised_by_id == w["dh_user"].id for g in grades)
    assert all(g.authorised_at is not None for g in grades)


async def test_authorise_flagged_requires_justification(
    async_session, dh_scenario,
):
    """Overriding a FLAG without justification raises 422."""
    w = dh_scenario
    svc = InstructorGradingService(async_session, grading_agent=_flag_agent())
    _bd, batch = await _build_full_batch(
        svc, w, score_grid=[(2, 10, 30), (3, 12, 25), (1, 8, 20)],
    )
    await svc.submit_batch(user_id=w["instr_user"].id, batch_id=batch.id)

    dh = DepartmentHeadGradingService(async_session)
    with pytest.raises(JustificationRequiredError):
        await dh.authorise(
            user_id=w["dh_user"].id, batch_id=batch.id,
            justification=None,
        )
    with pytest.raises(JustificationRequiredError):
        await dh.authorise(
            user_id=w["dh_user"].id, batch_id=batch.id,
            justification="   ",  # whitespace also rejected
        )


async def test_authorise_flagged_with_justification_overrides_agent(
    async_session, dh_scenario,
):
    """FLAGGED → AUTHORISED records decision=OVERRODE_AGENT_FLAG."""
    w = dh_scenario
    svc = InstructorGradingService(async_session, grading_agent=_flag_agent())
    _bd, batch = await _build_full_batch(
        svc, w, score_grid=[(2, 10, 30), (3, 12, 25), (1, 8, 20)],
    )
    await svc.submit_batch(user_id=w["instr_user"].id, batch_id=batch.id)

    dh = DepartmentHeadGradingService(async_session)
    result = await dh.authorise(
        user_id=w["dh_user"].id, batch_id=batch.id,
        justification=(
            "Remedial-makeup section; this distribution is expected by policy."
        ),
    )
    assert result.decision == "OVERRODE_AGENT_FLAG"
    assert result.new_status is GradeSubmissionStatus.AUTHORISED

    # Decision row exists with justification preserved.
    decision = (await async_session.execute(
        select(GradeAuthorisationDecision).where(
            GradeAuthorisationDecision.batch_id == batch.id,
        )
    )).scalar_one()
    assert decision.decision == "OVERRODE_AGENT_FLAG"
    assert "remedial" in decision.justification.lower()


# ── Reject paths ────────────────────────────────────────────────


async def test_reject_flagged_records_rejected_decision(
    async_session, dh_scenario,
):
    """FLAGGED + DH agrees → REJECTED."""
    w = dh_scenario
    svc = InstructorGradingService(async_session, grading_agent=_flag_agent())
    _bd, batch = await _build_full_batch(
        svc, w, score_grid=[(2, 10, 30), (3, 12, 25), (1, 8, 20)],
    )
    await svc.submit_batch(user_id=w["instr_user"].id, batch_id=batch.id)

    dh = DepartmentHeadGradingService(async_session)
    result = await dh.reject(
        user_id=w["dh_user"].id, batch_id=batch.id,
        justification="Please double-check the final exam scoring rubric.",
    )
    assert result.decision == "REJECTED"
    assert result.new_status is GradeSubmissionStatus.REJECTED

    grades = (await async_session.execute(
        select(Grade).where(Grade.course_id == w["course"].id)
    )).scalars().all()
    assert all(g.status is GradeSubmissionStatus.REJECTED for g in grades)


async def test_reject_submitted_records_overrode_approval(
    async_session, dh_scenario,
):
    """SUBMITTED + DH disagrees → OVERRODE_AGENT_APPROVAL."""
    w = dh_scenario
    svc = InstructorGradingService(async_session, grading_agent=_approve_agent())
    _bd, batch = await _build_full_batch(
        svc, w, score_grid=[(10, 50, 100), (9, 45, 90), (8, 40, 80)],
    )
    await svc.submit_batch(user_id=w["instr_user"].id, batch_id=batch.id)

    dh = DepartmentHeadGradingService(async_session)
    result = await dh.reject(
        user_id=w["dh_user"].id, batch_id=batch.id,
        justification="Cross-check failed — Abel's final score doesn't match the proctored result.",
    )
    assert result.decision == "OVERRODE_AGENT_APPROVAL"
    assert result.new_status is GradeSubmissionStatus.REJECTED


async def test_reject_always_requires_justification(
    async_session, dh_scenario,
):
    w = dh_scenario
    svc = InstructorGradingService(async_session, grading_agent=_approve_agent())
    _bd, batch = await _build_full_batch(
        svc, w, score_grid=[(10, 50, 100), (9, 45, 90), (8, 40, 80)],
    )
    await svc.submit_batch(user_id=w["instr_user"].id, batch_id=batch.id)

    dh = DepartmentHeadGradingService(async_session)
    with pytest.raises(JustificationRequiredError):
        await dh.reject(
            user_id=w["dh_user"].id, batch_id=batch.id,
            justification="",
        )


# ── Status gate ─────────────────────────────────────────────────


async def test_authorise_rejects_non_reviewable_states(
    async_session, dh_scenario,
):
    """Cannot authorise a DRAFT or already-AUTHORISED batch."""
    w = dh_scenario
    svc = InstructorGradingService(async_session, grading_agent=_approve_agent())
    _bd, batch = await _build_full_batch(
        svc, w, score_grid=[(10, 50, 100), (9, 45, 90), (8, 40, 80)],
    )
    dh = DepartmentHeadGradingService(async_session)
    with pytest.raises(GradeBatchNotReviewableError):
        # Batch is still DRAFT — instructor hasn't submitted.
        await dh.authorise(user_id=w["dh_user"].id, batch_id=batch.id)

    await svc.submit_batch(user_id=w["instr_user"].id, batch_id=batch.id)
    await dh.authorise(user_id=w["dh_user"].id, batch_id=batch.id)
    # Already AUTHORISED — second call rejected.
    with pytest.raises(GradeBatchNotReviewableError):
        await dh.authorise(user_id=w["dh_user"].id, batch_id=batch.id)


# ── REJECTED → DRAFT (instructor reopens after rejection) ──────


async def test_rejected_batch_can_be_reopened_by_instructor(
    async_session, dh_scenario,
):
    """After DH rejects, instructor reopens → DRAFT → edit → re-submit."""
    w = dh_scenario
    svc = InstructorGradingService(async_session, grading_agent=_approve_agent())
    _bd, batch = await _build_full_batch(
        svc, w, score_grid=[(10, 50, 100), (9, 45, 90), (8, 40, 80)],
    )
    await svc.submit_batch(user_id=w["instr_user"].id, batch_id=batch.id)
    dh = DepartmentHeadGradingService(async_session)
    await dh.reject(
        user_id=w["dh_user"].id, batch_id=batch.id,
        justification="Need to re-grade the final.",
    )

    # Instructor reopens the rejected batch — DRAFT.
    reopened = await svc.reopen_batch(
        user_id=w["instr_user"].id, batch_id=batch.id,
    )
    assert reopened.status is GradeSubmissionStatus.DRAFT
    assert reopened.iteration_count == 2


# ── Append-only decision history ───────────────────────────────


async def test_decision_history_is_append_only(
    async_session, dh_scenario,
):
    """Two rejections + a final authorise → 3 decision rows."""
    w = dh_scenario
    svc = InstructorGradingService(async_session, grading_agent=_approve_agent())
    _bd, batch = await _build_full_batch(
        svc, w, score_grid=[(10, 50, 100), (9, 45, 90), (8, 40, 80)],
    )
    dh = DepartmentHeadGradingService(async_session)

    # Round 1: submit → reject.
    await svc.submit_batch(user_id=w["instr_user"].id, batch_id=batch.id)
    await dh.reject(
        user_id=w["dh_user"].id, batch_id=batch.id,
        justification="Round 1 reject.",
    )
    # Instructor reopens + resubmits.
    await svc.reopen_batch(
        user_id=w["instr_user"].id, batch_id=batch.id,
    )
    await svc.submit_batch(user_id=w["instr_user"].id, batch_id=batch.id)
    # Round 2: reject again.
    await dh.reject(
        user_id=w["dh_user"].id, batch_id=batch.id,
        justification="Round 2 reject.",
    )
    await svc.reopen_batch(
        user_id=w["instr_user"].id, batch_id=batch.id,
    )
    await svc.submit_batch(user_id=w["instr_user"].id, batch_id=batch.id)
    # Round 3: authorise.
    await dh.authorise(user_id=w["dh_user"].id, batch_id=batch.id)

    decisions = (await async_session.execute(
        select(GradeAuthorisationDecision)
        .where(GradeAuthorisationDecision.batch_id == batch.id)
        .order_by(GradeAuthorisationDecision.decision_at)
    )).scalars().all()
    assert len(decisions) == 3
    assert decisions[0].decision == "OVERRODE_AGENT_APPROVAL"  # round 1
    assert decisions[1].decision == "OVERRODE_AGENT_APPROVAL"  # round 2
    assert decisions[2].decision == "AUTHORISED"               # round 3


# ── Review packet ──────────────────────────────────────────────


async def test_review_packet_carries_full_history(
    async_session, dh_scenario,
):
    """Packet returns batch + per-student grades + agent reviews + decisions."""
    w = dh_scenario
    svc = InstructorGradingService(async_session, grading_agent=_flag_agent())
    _bd, batch = await _build_full_batch(
        svc, w, score_grid=[(2, 10, 30), (3, 12, 25), (1, 8, 20)],
    )
    await svc.submit_batch(user_id=w["instr_user"].id, batch_id=batch.id)

    dh = DepartmentHeadGradingService(async_session)
    packet = await dh.get_review_packet(
        user_id=w["dh_user"].id, batch_id=batch.id,
    )
    assert packet.batch.id == batch.id
    assert len(packet.per_student_grades) == 3
    assert len(packet.agent_reviews) == 1
    assert packet.agent_reviews[0].verdict == "FLAG"
    assert packet.decisions == []


# ── rerun-agent ─────────────────────────────────────────────────


async def test_rerun_agent_replaces_pending_verdict(
    async_session, dh_scenario,
):
    """PENDING → DH triggers re-run with a working LLM → APPROVE."""
    w = dh_scenario
    # Round 1: agent with no LLM → PENDING.
    pending_agent = GradingMonitorAgent(llm_client=None)
    svc = InstructorGradingService(async_session, grading_agent=pending_agent)
    _bd, batch = await _build_full_batch(
        svc, w, score_grid=[(10, 50, 100), (9, 45, 90), (8, 40, 80)],
    )
    first = await svc.submit_batch(
        user_id=w["instr_user"].id, batch_id=batch.id,
    )
    assert first.agent_verdict == "PENDING"

    # DH triggers rerun with a working LLM.
    dh = DepartmentHeadGradingService(async_session, grading_agent=_approve_agent())
    result = await dh.rerun_agent(
        user_id=w["dh_user"].id, batch_id=batch.id,
    )
    assert result.agent_verdict == "APPROVE"
    assert result.new_status is GradeSubmissionStatus.SUBMITTED

    # Two review rows now exist, the latest being APPROVE.
    reviews = (await async_session.execute(
        select(GradeAgentReview)
        .where(GradeAgentReview.batch_id == batch.id)
        .order_by(GradeAgentReview.created_at)
    )).scalars().all()
    assert len(reviews) == 2
    assert reviews[0].verdict == "PENDING"
    assert reviews[1].verdict == "APPROVE"
