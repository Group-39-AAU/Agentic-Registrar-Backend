"""
Track A — Curriculum Compliance Agent contract tests.

Confirms the SDS Tables 64–66 contract: every public method returns
a structured :class:`ComplianceCheckResult` and the aggregate
:meth:`process_task` correctly conjuncts the three checks. The
agent is exercised against the SQLite test DB plus a per-test
:class:`PayMock` instance so global state is never observable
between tests.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from app.modules.course.agents import (
    ComplianceCheckResult,
    CurriculumComplianceAgent,
    MAX_CREDIT_LOAD_ECTS,
)
from app.modules.course.models import (
    Course, CoursePrerequisite, Registration, RegistrationCourse,
)
from app.modules.course.services import PayMock
from app.shared.enums import (
    AgentStatus, RegistrationStatus, SponsorshipType,
)


# ── Fixtures specific to compliance-agent tests ─────────────────


@pytest.fixture
def isolated_pay_mock() -> PayMock:
    """A per-test PayMock instance — never share the singleton."""
    return PayMock()


@pytest.fixture
def compliance_agent(isolated_pay_mock) -> CurriculumComplianceAgent:
    return CurriculumComplianceAgent(
        agent_id="AGENT_CCA_TEST",
        payment_service=isolated_pay_mock,
    )


@pytest_asyncio.fixture
async def cs_chain(async_session) -> dict[str, Course]:
    """Three CS courses with a 101 -> 201 -> 301 prereq chain."""
    cs101 = Course(
        code="CS101", title="Intro to Programming",
        credit_hours=4, semester=1, department="Computer Science",
    )
    cs201 = Course(
        code="CS201", title="Data Structures",
        credit_hours=4, semester=2, department="Computer Science",
    )
    cs301 = Course(
        code="CS301", title="Algorithms",
        credit_hours=4, semester=3, department="Computer Science",
    )
    async_session.add_all([cs101, cs201, cs301])
    await async_session.flush()

    async_session.add_all([
        CoursePrerequisite(course_id=cs201.id, prerequisite_course_id=cs101.id),
        CoursePrerequisite(course_id=cs301.id, prerequisite_course_id=cs201.id),
    ])
    await async_session.flush()
    return {"CS101": cs101, "CS201": cs201, "CS301": cs301}


@pytest_asyncio.fixture
async def registration_factory(
    async_session, seeded_term, seeded_student,
):
    """
    Factory that builds a Registration for the seeded student against
    a list of Course objects, returning the Registration with
    ``.courses`` already populated.
    """
    async def _factory(courses: list[Course]) -> Registration:
        reg = Registration(
            student_id=seeded_student.id,
            term_id=seeded_term.id,
            status=RegistrationStatus.REGISTRATION_OPEN,
            sponsorship_type=SponsorshipType.SELF_SPONSORED,
        )
        async_session.add(reg)
        await async_session.flush()
        for c in courses:
            async_session.add(
                RegistrationCourse(
                    registration_id=reg.id,
                    course_id=c.id,
                )
            )
        await async_session.flush()
        await async_session.refresh(reg, attribute_names=["courses"])
        return reg
    return _factory


# ── Construction & contract ─────────────────────────────────────


def test_agent_extends_course_base_agent_contract():
    from app.modules.course.agents import CourseBaseAgent
    agent = CurriculumComplianceAgent()
    assert isinstance(agent, CourseBaseAgent)
    assert agent.get_status() == AgentStatus.IDLE
    assert agent.agent_id.startswith("AGENT_CCA_")


# ── verify_prerequisites ────────────────────────────────────────


async def test_verify_prereqs_passes_for_root_course(
    async_session, compliance_agent, cs_chain, seeded_student,
):
    result = await compliance_agent.verify_prerequisites(
        async_session,
        seeded_student.id,
        cs_chain["CS101"].id,
        completed_course_ids=set(),
    )
    assert result.passed
    assert result.reasons == []


async def test_verify_prereqs_passes_when_completed_set_includes_prereq(
    async_session, compliance_agent, cs_chain, seeded_student,
):
    result = await compliance_agent.verify_prerequisites(
        async_session,
        seeded_student.id,
        cs_chain["CS201"].id,
        completed_course_ids={cs_chain["CS101"].id},
    )
    assert result.passed


async def test_verify_prereqs_fails_with_missing_course_codes(
    async_session, compliance_agent, cs_chain, seeded_student,
):
    result = await compliance_agent.verify_prerequisites(
        async_session,
        seeded_student.id,
        cs_chain["CS201"].id,
        completed_course_ids=set(),     # CS101 missing
    )
    assert result.passed is False
    assert any("CS101" in r for r in result.reasons)
    assert "CS101" in result.details["missing_course_codes"]


# ── validate_registration (22 ECTS ceiling) ─────────────────────


async def test_validate_registration_passes_under_ceiling(
    async_session, compliance_agent, cs_chain, registration_factory,
):
    reg = await registration_factory([cs_chain["CS101"], cs_chain["CS201"]])
    result = await compliance_agent.validate_registration(async_session, reg)
    assert result.passed
    assert result.details["total_credits"] == 8
    assert result.details["ceiling"] == MAX_CREDIT_LOAD_ECTS


async def test_validate_registration_fails_over_ceiling(
    async_session, compliance_agent, registration_factory,
):
    """Six 4-credit courses -> 24 ECTS, breaches the 22 ECTS ceiling."""
    bulky_courses = []
    for i in range(6):
        c = Course(
            code=f"BULK{i:03d}", title=f"Bulky course {i}",
            credit_hours=4, semester=1, department="Test",
        )
        async_session.add(c)
        bulky_courses.append(c)
    await async_session.flush()

    reg = await registration_factory(bulky_courses)
    result = await compliance_agent.validate_registration(async_session, reg)

    assert result.passed is False
    assert result.details["total_credits"] == 24
    assert any("22" in r for r in result.reasons)


async def test_validate_registration_fails_on_empty(
    async_session, compliance_agent, registration_factory,
):
    reg = await registration_factory([])
    result = await compliance_agent.validate_registration(async_session, reg)
    assert result.passed is False
    assert result.details["total_credits"] == 0


async def test_validate_registration_excludes_dropped_courses(
    async_session, compliance_agent, cs_chain, registration_factory,
):
    reg = await registration_factory([cs_chain["CS101"], cs_chain["CS201"]])
    # Drop one course
    reg.courses[0].is_dropped = True
    await async_session.flush()

    result = await compliance_agent.validate_registration(async_session, reg)
    assert result.passed
    assert result.details["total_credits"] == 4   # only the non-dropped course


# ── check_payment_status ────────────────────────────────────────


def test_check_payment_passes_when_all_paid(
    compliance_agent, isolated_pay_mock,
):
    student_id = uuid.uuid4()
    course_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    for cid in course_ids:
        isolated_pay_mock.set_payment_status(student_id, cid, paid=True)

    result = compliance_agent.check_payment_status(student_id, course_ids)
    assert result.passed
    assert result.details["checked_course_count"] == 3


def test_check_payment_fails_when_any_unpaid(
    compliance_agent, isolated_pay_mock,
):
    student_id = uuid.uuid4()
    paid_id = uuid.uuid4()
    unpaid_id = uuid.uuid4()
    isolated_pay_mock.set_payment_status(student_id, paid_id, paid=True)

    result = compliance_agent.check_payment_status(
        student_id, [paid_id, unpaid_id]
    )
    assert result.passed is False
    assert str(unpaid_id) in result.details["unpaid_course_ids"]
    assert str(paid_id) not in result.details["unpaid_course_ids"]


# ── process_task aggregate ──────────────────────────────────────


async def test_process_task_aggregates_to_pass_when_everything_clean(
    async_session,
    compliance_agent,
    isolated_pay_mock,
    cs_chain,
    registration_factory,
    seeded_student,
):
    reg = await registration_factory([cs_chain["CS201"]])
    isolated_pay_mock.set_payment_status(
        seeded_student.id, cs_chain["CS201"].id, paid=True,
    )

    out = await compliance_agent.process_task({
        "session": async_session,
        "registration": reg,
        "completed_course_ids": {cs_chain["CS101"].id},
    })

    assert out["overall_passed"] is True
    assert out["load_result"]["passed"] is True
    assert out["payment_result"]["passed"] is True
    assert all(p["passed"] for p in out["prereq_results"])


async def test_process_task_fails_when_prereq_missing(
    async_session,
    compliance_agent,
    isolated_pay_mock,
    cs_chain,
    registration_factory,
    seeded_student,
):
    reg = await registration_factory([cs_chain["CS201"]])
    isolated_pay_mock.set_payment_status(
        seeded_student.id, cs_chain["CS201"].id, paid=True,
    )

    out = await compliance_agent.process_task({
        "session": async_session,
        "registration": reg,
        "completed_course_ids": set(),    # CS101 missing
    })
    assert out["overall_passed"] is False
    assert any(not p["passed"] for p in out["prereq_results"])


async def test_process_task_fails_when_payment_missing(
    async_session,
    compliance_agent,
    cs_chain,
    registration_factory,
    seeded_student,
):
    """isolated_pay_mock is empty by default → all courses unpaid."""
    reg = await registration_factory([cs_chain["CS101"]])
    out = await compliance_agent.process_task({
        "session": async_session,
        "registration": reg,
        "completed_course_ids": set(),
    })
    assert out["overall_passed"] is False
    assert out["payment_result"]["passed"] is False
