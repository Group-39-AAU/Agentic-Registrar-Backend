"""
Track A — AcademicAdvisoryAgent demand-driven consult flow.

Covers the LLM-first consultation path the student invokes from
their portal. The contract is "no caller-provided student state":

  - Pre-registration: empty body. Server resolves the open term,
    student profile, completed courses + CGPA from the Grade ledger.
  - Registration-plan: empty body. Server reads the student's
    in-progress draft Registration for the open term.
  - Add-drop: body carries only the proposed delta (course IDs to
    add and/or drop). Server reads the active Registration.

LLMUnavailableError surfaces 503 — there is NO rule-based fallback
for the consult flow because it is explicitly LLM-first.

The Gemini SDK is never called for real; we inject a stub LLMClient
subclass whose ``consult_academic_plan`` is fully under test
control.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.ai.llm_client import LLMClient, LLMUnavailableError
from app.modules.course.agents import (
    AcademicAdvisoryAgent, ConsultationResult,
)
from app.modules.course.exceptions import EntityNotFoundError
from app.modules.course.models import (
    AdvisoryRecommendation, Course, CoursePrerequisite, Grade, Registration,
    RegistrationCourse,
)
from app.modules.course.repository import GradeRepository
from app.modules.course.service import AdvisoryService
from app.shared.enums import (
    ConsultationMode, GradeLetter, GradeSubmissionStatus, RegistrationStatus,
    RiskStatus, SponsorshipType,
)


# ── Stub LLM clients ────────────────────────────────────────────


class _CannedConsultLLM(LLMClient):
    """LLMClient subclass returning a fixed JSON dict for consult calls."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._client = MagicMock()
        self._model = "gemini-2.5-flash-lite"
        self._timeout = 5.0
        self._max_tokens = 600
        self._payload = payload
        self.calls: list[dict[str, Any]] = []

    async def consult_academic_plan(
        self, consultation_payload: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append(consultation_payload)
        return self._payload


class _BoomConsultLLM(LLMClient):
    """LLMClient subclass that hard-fails the consult call."""

    def __init__(self) -> None:
        self._client = MagicMock()
        self._model = "gemini-2.5-flash-lite"
        self._timeout = 5.0
        self._max_tokens = 600

    async def consult_academic_plan(
        self, consultation_payload: dict[str, Any],
    ) -> dict[str, Any]:
        raise LLMUnavailableError("Simulated Gemini outage.")


def _llm_payload(
    *,
    verdict: str = "ON_TRACK",
    risk: str = "LOW",
    courses: Optional[list[dict[str, Any]]] = None,
    warnings: Optional[list[str]] = None,
    impact: Optional[dict[str, Any]] = None,
    narrative: str = "You are on a healthy path; keep going.",
) -> dict[str, Any]:
    return {
        "verdict": verdict,
        "risk_status": risk,
        "recommended_courses": courses or [],
        "warnings": warnings or [],
        "graduation_impact": impact or {
            "semesters_remaining": 6,
            "on_track": True,
            "expected_graduation_semester": 8,
            "delay_semesters": 0,
            "critical_path_courses": [],
        },
        "narrative": narrative,
    }


# ── Fixtures ────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def cs_curriculum_with_prereqs(async_session) -> dict[str, Course]:
    """
    A 4-course CS curriculum with one prereq edge so the agent's
    prerequisite-graph builder is exercised:

        CS101 (sem 1)  — no prereqs
        CS102 (sem 1)  — no prereqs
        CS201 (sem 2)  — requires CS101
        CS301 (sem 3)  — requires CS201
    """
    by_code: dict[str, Course] = {}
    for code, sem in (("CS101", 1), ("CS102", 1), ("CS201", 2), ("CS301", 3)):
        c = Course(
            code=code, title=f"{code} title", credit_hours=3,
            semester=sem, department="Computer Science",
        )
        async_session.add(c)
        by_code[code] = c
    await async_session.flush()

    async_session.add(CoursePrerequisite(
        course_id=by_code["CS201"].id,
        prerequisite_course_id=by_code["CS101"].id,
    ))
    async_session.add(CoursePrerequisite(
        course_id=by_code["CS301"].id,
        prerequisite_course_id=by_code["CS201"].id,
    ))
    await async_session.flush()
    return by_code


def _student_with_dept(student, department="Computer Science"):
    """Patch a Student fixture's denormalised department in-place."""
    student.department = department
    return student


def _add_grade(
    session, *, student, course, term, letter, status=GradeSubmissionStatus.AUTHORISED,
):
    from app.modules.course.grade_points import points_for
    pts = points_for(letter)
    session.add(Grade(
        student_id=student.id,
        course_id=course.id,
        term_id=term.id,
        letter_grade=letter,
        credit_hours=course.credit_hours,
        grade_points=pts * course.credit_hours if pts is not None else None,
        status=status,
    ))


# ── GradeRepository: CGPA + completed-course resolution ─────────


async def test_grade_repository_returns_none_cgpa_for_fresh_student(
    async_session, seeded_student,
):
    """No AUTHORISED grades → CGPA is None (not 0.0)."""
    repo = GradeRepository(async_session)
    cgpa = await repo.compute_cgpa(seeded_student.id)
    completed = await repo.completed_course_ids(seeded_student.id)
    assert cgpa is None
    assert completed == set()


async def test_grade_repository_credit_weighted_cgpa(
    async_session, seeded_student, seeded_term, cs_curriculum_with_prereqs,
):
    """
    Two AUTHORISED grades — A (3 credits) + C (3 credits) — should
    average to 3.0 CGPA: (4.0*3 + 2.0*3) / 6 = 3.0.
    """
    _add_grade(
        async_session, student=seeded_student,
        course=cs_curriculum_with_prereqs["CS101"],
        term=seeded_term, letter=GradeLetter.A,
    )
    _add_grade(
        async_session, student=seeded_student,
        course=cs_curriculum_with_prereqs["CS102"],
        term=seeded_term, letter=GradeLetter.C,
    )
    await async_session.flush()

    repo = GradeRepository(async_session)
    cgpa = await repo.compute_cgpa(seeded_student.id)
    completed = await repo.completed_course_ids(seeded_student.id)
    assert cgpa == 3.0
    assert completed == {
        cs_curriculum_with_prereqs["CS101"].id,
        cs_curriculum_with_prereqs["CS102"].id,
    }


async def test_grade_repository_excludes_non_authorised_grades(
    async_session, seeded_student, seeded_term, cs_curriculum_with_prereqs,
):
    """DRAFT and REJECTED grades must not affect CGPA."""
    _add_grade(
        async_session, student=seeded_student,
        course=cs_curriculum_with_prereqs["CS101"],
        term=seeded_term, letter=GradeLetter.A,
        status=GradeSubmissionStatus.AUTHORISED,
    )
    _add_grade(
        async_session, student=seeded_student,
        course=cs_curriculum_with_prereqs["CS102"],
        term=seeded_term, letter=GradeLetter.A,
        status=GradeSubmissionStatus.DRAFT,
    )
    _add_grade(
        async_session, student=seeded_student,
        course=cs_curriculum_with_prereqs["CS201"],
        term=seeded_term, letter=GradeLetter.A,
        status=GradeSubmissionStatus.REJECTED,
    )
    await async_session.flush()

    repo = GradeRepository(async_session)
    assert await repo.compute_cgpa(seeded_student.id) == 4.0
    assert (await repo.completed_course_ids(seeded_student.id)) == {
        cs_curriculum_with_prereqs["CS101"].id,
    }


async def test_grade_repository_f_counts_in_cgpa_but_not_completed(
    async_session, seeded_student, seeded_term, cs_curriculum_with_prereqs,
):
    """
    F counts as 0.0 toward CGPA but does NOT add to the completed
    set (per AAU rule: D is the lowest passing grade).
    """
    _add_grade(
        async_session, student=seeded_student,
        course=cs_curriculum_with_prereqs["CS101"],
        term=seeded_term, letter=GradeLetter.B,    # 3.0 × 3 = 9
    )
    _add_grade(
        async_session, student=seeded_student,
        course=cs_curriculum_with_prereqs["CS102"],
        term=seeded_term, letter=GradeLetter.F,    # 0.0 × 3 = 0
    )
    await async_session.flush()

    repo = GradeRepository(async_session)
    assert await repo.compute_cgpa(seeded_student.id) == 1.5  # 9 / 6
    assert (await repo.completed_course_ids(seeded_student.id)) == {
        cs_curriculum_with_prereqs["CS101"].id,
    }


async def test_grade_repository_excludes_i_and_ng_from_cgpa(
    async_session, seeded_student, seeded_term, cs_curriculum_with_prereqs,
):
    """I and NG route through the standing-agent edge case, not CGPA."""
    _add_grade(
        async_session, student=seeded_student,
        course=cs_curriculum_with_prereqs["CS101"],
        term=seeded_term, letter=GradeLetter.A,
    )
    _add_grade(
        async_session, student=seeded_student,
        course=cs_curriculum_with_prereqs["CS102"],
        term=seeded_term, letter=GradeLetter.I,
    )
    _add_grade(
        async_session, student=seeded_student,
        course=cs_curriculum_with_prereqs["CS201"],
        term=seeded_term, letter=GradeLetter.NG,
    )
    await async_session.flush()

    repo = GradeRepository(async_session)
    # CGPA from CS101 only: 4.0 × 3 / 3 = 4.0
    assert await repo.compute_cgpa(seeded_student.id) == 4.0


# ── Agent: payload construction ─────────────────────────────────


async def test_agent_consult_passes_curriculum_and_history_to_llm(
    async_session, cs_curriculum_with_prereqs,
):
    fake = _CannedConsultLLM(_llm_payload())
    agent = AcademicAdvisoryAgent(llm_client=fake)

    await agent.consult(
        async_session,
        mode=ConsultationMode.PRE_REGISTRATION,
        student_context={
            "student_id": "UGR/0042/14",
            "department": "Computer Science",
            "current_semester": 2,
            "cgpa": 3.2,
            "sponsorship_type": SponsorshipType.GOVERNMENT.value,
        },
        completed_course_ids={cs_curriculum_with_prereqs["CS101"].id},
    )

    payload = fake.calls[0]
    assert payload["mode"] == "PRE_REGISTRATION"
    assert payload["student"]["current_semester"] == 2
    assert payload["student"]["cgpa"] == 3.2
    assert payload["history"]["completed_course_codes"] == ["CS101"]
    codes = {c["course_code"]: c for c in payload["department"]["curriculum"]}
    assert set(codes) == {"CS101", "CS102", "CS201", "CS301"}
    assert codes["CS201"]["prerequisite_codes"] == ["CS101"]
    assert codes["CS301"]["prerequisite_codes"] == ["CS201"]
    assert codes["CS101"]["prerequisite_codes"] == []


async def test_agent_tolerates_null_cgpa(
    async_session, cs_curriculum_with_prereqs,
):
    """Fresh first-semester student → CGPA flows through as None."""
    fake = _CannedConsultLLM(_llm_payload())
    agent = AcademicAdvisoryAgent(llm_client=fake)

    await agent.consult(
        async_session,
        mode=ConsultationMode.PRE_REGISTRATION,
        student_context={
            "student_id": "UGR/0001/14",
            "department": "Computer Science",
            "current_semester": 1,
            "cgpa": None,
            "sponsorship_type": SponsorshipType.GOVERNMENT.value,
        },
        completed_course_ids=set(),
    )
    assert fake.calls[0]["student"]["cgpa"] is None


# ── Agent: hard-constraint validation ───────────────────────────


async def test_agent_filters_recommendations_for_already_completed_courses(
    async_session, cs_curriculum_with_prereqs,
):
    fake = _CannedConsultLLM(_llm_payload(
        courses=[
            {
                "course_code": "CS101",  # already completed!
                "title": "CS101 title",
                "credit_hours": 3,
                "is_core": True,
                "reason": "LLM forgot to check completion.",
            },
            {
                # CS201 is sem 2 — parity-matches the sem-2 student
                # so the only filter that fires is "already completed"
                # on CS101.
                "course_code": "CS201",
                "title": "CS201 title",
                "credit_hours": 3,
                "is_core": True,
                "reason": "Valid suggestion.",
            },
        ],
    ))
    agent = AcademicAdvisoryAgent(llm_client=fake)

    result: ConsultationResult = await agent.consult(
        async_session,
        mode=ConsultationMode.PRE_REGISTRATION,
        student_context={
            "department": "Computer Science",
            "current_semester": 2,
            "cgpa": 3.0,
        },
        completed_course_ids={cs_curriculum_with_prereqs["CS101"].id},
    )

    kept_codes = [r["course_code"] for r in result.recommended_courses]
    assert kept_codes == ["CS201"]
    assert any("CS101" in f for f in result.filtered_recommendations)
    assert any("filtered_recommendations" in w for w in result.warnings)


async def test_agent_filters_recommendations_for_invented_codes(
    async_session, cs_curriculum_with_prereqs,
):
    fake = _CannedConsultLLM(_llm_payload(
        courses=[{
            "course_code": "CS999",  # doesn't exist
            "title": "Phantom Course",
            "credit_hours": 3,
            "is_core": True,
            "reason": "LLM made this up.",
        }],
    ))
    agent = AcademicAdvisoryAgent(llm_client=fake)

    result = await agent.consult(
        async_session,
        mode=ConsultationMode.PRE_REGISTRATION,
        student_context={
            "department": "Computer Science",
            "current_semester": 1,
            "cgpa": 3.0,
        },
        completed_course_ids=set(),
    )
    assert result.recommended_courses == []
    assert any("CS999" in f for f in result.filtered_recommendations)


async def test_agent_unknown_verdict_normalised_to_needs_review(
    async_session, cs_curriculum_with_prereqs,
):
    fake = _CannedConsultLLM(_llm_payload(verdict="UNKNOWN_VALUE"))
    agent = AcademicAdvisoryAgent(llm_client=fake)
    result = await agent.consult(
        async_session,
        mode=ConsultationMode.PRE_REGISTRATION,
        student_context={
            "department": "Computer Science",
            "current_semester": 1, "cgpa": 3.0,
        },
        completed_course_ids=set(),
    )
    assert result.verdict == "NEEDS_REVIEW"


async def test_agent_unknown_risk_status_normalised_to_medium(
    async_session, cs_curriculum_with_prereqs,
):
    fake = _CannedConsultLLM(_llm_payload(risk="EXTREME"))
    agent = AcademicAdvisoryAgent(llm_client=fake)
    result = await agent.consult(
        async_session,
        mode=ConsultationMode.PRE_REGISTRATION,
        student_context={
            "department": "Computer Science",
            "current_semester": 1, "cgpa": 3.0,
        },
        completed_course_ids=set(),
    )
    assert result.risk_status == RiskStatus.MEDIUM


# ── Agent: hard-fail when LLM is unavailable ────────────────────


async def test_agent_consult_raises_when_no_llm_client(
    async_session, cs_curriculum_with_prereqs,
):
    agent = AcademicAdvisoryAgent()  # no llm_client
    with pytest.raises(LLMUnavailableError):
        await agent.consult(
            async_session,
            mode=ConsultationMode.PRE_REGISTRATION,
            student_context={
                "department": "Computer Science",
                "current_semester": 1, "cgpa": 3.0,
            },
            completed_course_ids=set(),
        )


async def test_agent_consult_propagates_llm_failure(
    async_session, cs_curriculum_with_prereqs,
):
    agent = AcademicAdvisoryAgent(llm_client=_BoomConsultLLM())
    with pytest.raises(LLMUnavailableError, match="outage"):
        await agent.consult(
            async_session,
            mode=ConsultationMode.PRE_REGISTRATION,
            student_context={
                "department": "Computer Science",
                "current_semester": 1, "cgpa": 3.0,
            },
            completed_course_ids=set(),
        )


# ── Service: server-resolves everything (no caller inputs) ──────


async def test_service_pre_registration_resolves_term_and_cgpa_from_db(
    async_session, seeded_student, seeded_term, cs_curriculum_with_prereqs,
):
    """
    Caller passes only the student_id. Server finds the open term,
    pulls AUTHORISED grades for CGPA + completed-course set, and
    builds the consult payload from there.
    """
    _student_with_dept(seeded_student)
    _add_grade(
        async_session, student=seeded_student,
        course=cs_curriculum_with_prereqs["CS101"],
        term=seeded_term, letter=GradeLetter.B_PLUS,   # 3.5 × 3 = 10.5
    )
    await async_session.flush()

    fake = _CannedConsultLLM(_llm_payload())
    agent = AcademicAdvisoryAgent(llm_client=fake)
    svc = AdvisoryService(async_session, advisory_agent=agent)

    rec = await svc.consult_pre_registration(student_id=seeded_student.id)

    assert rec.consultation_mode == ConsultationMode.PRE_REGISTRATION
    assert rec.term_id == seeded_term.id
    payload = fake.calls[0]
    assert payload["student"]["cgpa"] == 3.5
    assert payload["history"]["completed_course_codes"] == ["CS101"]


async def test_service_pre_registration_404_when_no_open_term(
    async_session, seeded_student,
):
    """No open AcademicTerm → EntityNotFoundError → 404."""
    _student_with_dept(seeded_student)
    fake = _CannedConsultLLM(_llm_payload())
    agent = AcademicAdvisoryAgent(llm_client=fake)
    svc = AdvisoryService(async_session, advisory_agent=agent)

    with pytest.raises(EntityNotFoundError, match="AcademicTerm"):
        await svc.consult_pre_registration(student_id=seeded_student.id)


async def test_service_registration_plan_reads_in_progress_draft(
    async_session, seeded_student, seeded_term, cs_curriculum_with_prereqs,
):
    """
    Server reads the student's draft Registration (status not in
    {REGISTERED, ADD_DROP_WINDOW, CANCELLED}) and uses its active
    courses as the proposed plan. No caller inputs.
    """
    _student_with_dept(seeded_student)
    draft = Registration(
        student_id=seeded_student.id,
        term_id=seeded_term.id,
        status=RegistrationStatus.REGISTRATION_OPEN,    # draft state
        sponsorship_type=SponsorshipType.GOVERNMENT,
    )
    async_session.add(draft)
    await async_session.flush()
    async_session.add_all([
        RegistrationCourse(
            registration_id=draft.id,
            course_id=cs_curriculum_with_prereqs["CS101"].id,
            is_dropped=False,
        ),
        RegistrationCourse(
            registration_id=draft.id,
            course_id=cs_curriculum_with_prereqs["CS102"].id,
            is_dropped=False,
        ),
    ])
    await async_session.flush()

    fake = _CannedConsultLLM(_llm_payload())
    agent = AcademicAdvisoryAgent(llm_client=fake)
    svc = AdvisoryService(async_session, advisory_agent=agent)

    await svc.consult_registration_plan(student_id=seeded_student.id)

    payload = fake.calls[0]
    assert payload["mode"] == "REGISTRATION_PLAN"
    assert sorted(payload["draft"]["proposed_course_codes"]) == [
        "CS101", "CS102",
    ]


async def test_service_registration_plan_404_when_no_draft_exists(
    async_session, seeded_student, seeded_term, cs_curriculum_with_prereqs,
):
    """
    Student with no in-progress draft for the open term → 404 so the
    portal can route them to the pre-registration consult instead.
    """
    _student_with_dept(seeded_student)
    fake = _CannedConsultLLM(_llm_payload())
    agent = AcademicAdvisoryAgent(llm_client=fake)
    svc = AdvisoryService(async_session, advisory_agent=agent)

    with pytest.raises(EntityNotFoundError, match="RegistrationDraft"):
        await svc.consult_registration_plan(student_id=seeded_student.id)


async def test_service_add_drop_resolves_active_registration(
    async_session, seeded_student, seeded_term, cs_curriculum_with_prereqs,
):
    """
    Caller provides ONLY the proposed delta. Server finds the
    REGISTERED registration in the open term and reads its active
    courses as the current plan.
    """
    _student_with_dept(seeded_student)
    registration = Registration(
        student_id=seeded_student.id,
        term_id=seeded_term.id,
        status=RegistrationStatus.REGISTERED,
        sponsorship_type=SponsorshipType.GOVERNMENT,
    )
    async_session.add(registration)
    await async_session.flush()
    async_session.add_all([
        RegistrationCourse(
            registration_id=registration.id,
            course_id=cs_curriculum_with_prereqs["CS102"].id,
            is_dropped=False,
        ),
        RegistrationCourse(
            registration_id=registration.id,
            course_id=cs_curriculum_with_prereqs["CS201"].id,
            is_dropped=False,
        ),
    ])
    await async_session.flush()

    fake = _CannedConsultLLM(_llm_payload())
    agent = AcademicAdvisoryAgent(llm_client=fake)
    svc = AdvisoryService(async_session, advisory_agent=agent)

    await svc.consult_add_drop(
        student_id=seeded_student.id,
        add_course_ids=[cs_curriculum_with_prereqs["CS301"].id],
        drop_course_ids=[cs_curriculum_with_prereqs["CS201"].id],
    )

    payload = fake.calls[0]
    assert payload["mode"] == "ADD_DROP"
    assert sorted(payload["draft"]["proposed_course_codes"]) == [
        "CS102", "CS201",
    ]
    assert payload["proposed_changes"]["add_course_codes"] == ["CS301"]
    assert payload["proposed_changes"]["drop_course_codes"] == ["CS201"]


async def test_service_add_drop_proactive_mode_with_empty_deltas(
    async_session, seeded_student, seeded_term, cs_curriculum_with_prereqs,
):
    """
    Proactive add/drop: caller sends no proposed changes. Server
    still resolves the active registration, but the consult payload
    carries empty add/drop arrays so the LLM switches into "what
    should I change?" mode and returns its own recommendations.
    """
    _student_with_dept(seeded_student)
    registration = Registration(
        student_id=seeded_student.id,
        term_id=seeded_term.id,
        status=RegistrationStatus.REGISTERED,
        sponsorship_type=SponsorshipType.GOVERNMENT,
    )
    async_session.add(registration)
    await async_session.flush()
    async_session.add_all([
        RegistrationCourse(
            registration_id=registration.id,
            course_id=cs_curriculum_with_prereqs["CS102"].id,
            is_dropped=False,
        ),
        RegistrationCourse(
            registration_id=registration.id,
            course_id=cs_curriculum_with_prereqs["CS201"].id,
            is_dropped=False,
        ),
    ])
    await async_session.flush()

    # LLM returns proactive recommendations of its own — caller
    # didn't ask for a specific change, so the agent's job is to
    # propose one.
    fake = _CannedConsultLLM(_llm_payload(
        verdict="AT_RISK",
        risk="MEDIUM",
        courses=[{
            "course_code": "CS301",
            "title": "CS301 title",
            "credit_hours": 3,
            "is_core": True,
            "reason": "Add CS301 next — it unlocks the senior sequence.",
        }],
        warnings=["Consider dropping CS201 if load feels heavy."],
        narrative="Your plan is workable but you could add CS301 to stay on pace.",
    ))
    agent = AcademicAdvisoryAgent(llm_client=fake)
    svc = AdvisoryService(async_session, advisory_agent=agent)

    rec = await svc.consult_add_drop(student_id=seeded_student.id)

    payload = fake.calls[0]
    assert payload["mode"] == "ADD_DROP"
    # Active registration courses still flow through as the
    # current "draft" so the LLM can reason about them.
    assert sorted(payload["draft"]["proposed_course_codes"]) == [
        "CS102", "CS201",
    ]
    # Empty proposed_changes is the proactive-mode signal.
    assert payload["proposed_changes"]["add_course_codes"] == []
    assert payload["proposed_changes"]["drop_course_codes"] == []
    # The recommendations the student sees are the LLM's proactive picks.
    assert any(
        c["course_code"] == "CS301" for c in rec.recommended_courses
    )
    assert rec.gap_analysis["verdict"] == "AT_RISK"


async def test_service_add_drop_404_when_student_not_registered(
    async_session, seeded_student, seeded_term, cs_curriculum_with_prereqs,
):
    """Student without a REGISTERED row in the open term → 404."""
    _student_with_dept(seeded_student)
    fake = _CannedConsultLLM(_llm_payload())
    agent = AcademicAdvisoryAgent(llm_client=fake)
    svc = AdvisoryService(async_session, advisory_agent=agent)

    with pytest.raises(EntityNotFoundError, match="Registration"):
        await svc.consult_add_drop(
            student_id=seeded_student.id,
            add_course_ids=[cs_curriculum_with_prereqs["CS101"].id],
        )


async def test_service_consult_propagates_llm_unavailable_to_caller(
    async_session, seeded_student, seeded_term, cs_curriculum_with_prereqs,
):
    """LLMUnavailableError bubbles up so the router maps it to 503."""
    _student_with_dept(seeded_student)
    agent = AcademicAdvisoryAgent(llm_client=_BoomConsultLLM())
    svc = AdvisoryService(async_session, advisory_agent=agent)

    with pytest.raises(LLMUnavailableError):
        await svc.consult_pre_registration(student_id=seeded_student.id)


# ── Round-trip: consult rows coexist with rule-engine rows ──────


async def test_consult_and_rule_rows_share_table_with_distinct_modes(
    async_session, seeded_student, seeded_term, cs_curriculum_with_prereqs,
):
    """
    Both writers (rule-engine evaluate_plan + LLM consult_*) land in
    advisory_recommendations. Distinguishable by ``consultation_mode``:
    NULL = rule, non-NULL = consult.
    """
    _student_with_dept(seeded_student)
    fake = _CannedConsultLLM(_llm_payload())
    agent = AcademicAdvisoryAgent(llm_client=fake)
    svc = AdvisoryService(async_session, advisory_agent=agent)

    # Rule-based submit-time evaluation (mode left NULL)
    await svc.evaluate_plan(
        student_id=seeded_student.id,
        term_id=seeded_term.id,
        proposed_course_ids=[cs_curriculum_with_prereqs["CS101"].id],
        cgpa=3.5,
        completed_course_ids=set(),
    )
    # LLM consult (mode = PRE_REGISTRATION)
    await svc.consult_pre_registration(student_id=seeded_student.id)

    rows = (
        await async_session.execute(
            select(AdvisoryRecommendation).where(
                AdvisoryRecommendation.student_id == seeded_student.id
            )
        )
    ).scalars().all()
    modes = {r.consultation_mode for r in rows}
    assert None in modes
    assert ConsultationMode.PRE_REGISTRATION in modes
