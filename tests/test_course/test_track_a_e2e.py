"""
Track A — end-to-end integration tests.

Each test composes multiple Track A services to walk a full
cross-feature flow that the per-feature unit tests don't cover on
their own. Driven through the service layer to stay HTTP-
dependency-free, mirroring the Track A.4 / A.5 endpoint test
convention.

Flows covered:
  1. Full registration lifecycle: draft -> submit -> REGISTERED
     and its PAYMENT_HOLD branch.
  2. Schedule generation populates the student's timetable read view.
  3. Add/drop after registration: ADD a paid course, then DROP it,
     each with the full state-machine transition.
  4. Advisory HIGH-risk verdict routes to the officer queue and
     can be closed by an officer.
  5. Prerequisite block bounces the registration back to draft;
     the student can resubmit without the offending course.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.modules.course.agents import (
    AcademicAdvisoryAgent, AcademicSchedulingAgent,
    CurriculumComplianceAgent, EnrollmentAdjustmentAgent,
)
from app.modules.course.exceptions import (
    AdjustmentDeniedError, ComplianceCheckFailedError,
)
from app.modules.course.models import (
    AcademicTerm, AddDropRequest, AdvisoryRecommendation,
    Course, CourseOffering, CoursePrerequisite, Instructor,
    Registration, RegistrationCourse, Section, Student,
)
from app.modules.course.service import (
    AddDropService, AdvisoryService, RegistrationService,
    SchedulingService,
)
from app.modules.course.services import PayMock
from app.modules.auth.models import User
from app.shared.enums import (
    AddDropAction, AddDropRequestStatus, RegistrationStatus, RiskStatus,
    SponsorshipType, UserRole,
)


# ── Shared fixtures ──────────────────────────────────────────────


@pytest.fixture
def isolated_pay_mock() -> PayMock:
    return PayMock()


@pytest_asyncio.fixture
async def open_term(async_session, seeded_term):
    seeded_term.is_open = True
    await async_session.flush()
    return seeded_term


@pytest_asyncio.fixture
async def cs_catalog(async_session, open_term, seeded_instructor):
    """
    Curriculum + offerings + sections needed for the cross-feature
    flows. CS101 → CS201 prereq chain, plus a stand-alone MATH101.
    Each course gets a single offering (in open_term) and one
    section assigned to ``seeded_instructor``.
    """
    cs101 = Course(code="CS101", title="Intro", credit_hours=4,
                   semester=1, department="Computer Science")
    cs201 = Course(code="CS201", title="Data Structures", credit_hours=4,
                   semester=2, department="Computer Science")
    math101 = Course(code="MATH101", title="Calc I", credit_hours=4,
                     semester=1, department="Mathematics")
    async_session.add_all([cs101, cs201, math101])
    await async_session.flush()

    async_session.add(
        CoursePrerequisite(course_id=cs201.id, prerequisite_course_id=cs101.id)
    )

    offerings_sections: dict[str, dict] = {}
    for course in (cs101, cs201, math101):
        offering = CourseOffering(
            course_id=course.id, term_id=open_term.id,
            capacity=30, section_count=1,
        )
        async_session.add(offering)
        await async_session.flush()
        section = Section(
            offering_id=offering.id, section_code="A",
            room=f"NB-10{len(offerings_sections)+1}",
            time_slot=f"DAY{len(offerings_sections)} 08:30-10:00",
            instructor_id=seeded_instructor.id,
            capacity=30, enrolled_count=0,
        )
        async_session.add(section)
        await async_session.flush()
        offerings_sections[course.code] = {
            "course": course, "offering": offering, "section": section,
        }
    return offerings_sections


@pytest.fixture
def reg_service(async_session, isolated_pay_mock):
    agent = CurriculumComplianceAgent(
        agent_id="AGENT_CCA_E2E",
        payment_service=isolated_pay_mock,
    )
    return RegistrationService(async_session, compliance_agent=agent)


@pytest.fixture
def add_drop_service(async_session, isolated_pay_mock):
    agent = EnrollmentAdjustmentAgent(
        agent_id="AGENT_EAA_E2E",
        payment_service=isolated_pay_mock,
    )
    return AddDropService(async_session, adjustment_agent=agent)


@pytest.fixture
def sched_service(async_session):
    agent = AcademicSchedulingAgent(agent_id="AGENT_ASA_E2E")
    return SchedulingService(async_session, scheduling_agent=agent)


@pytest.fixture
def advisory_service(async_session):
    agent = AcademicAdvisoryAgent(agent_id="AGENT_AAA_E2E")
    return AdvisoryService(async_session, advisory_agent=agent)


# ── Flow 1: Full happy-path registration lifecycle ──────────────


async def test_full_registration_lifecycle_reaches_REGISTERED(
    async_session, reg_service, isolated_pay_mock,
    open_term, seeded_student, cs_catalog,
):
    """draft -> add courses -> mark paid -> submit -> REGISTERED."""
    reg = await reg_service.create_draft(
        seeded_student.id, open_term.id, SponsorshipType.SELF_SPONSORED,
    )
    await reg_service.add_course_to_draft(
        reg.id, cs_catalog["CS101"]["course"].id,
    )
    await reg_service.add_course_to_draft(
        reg.id, cs_catalog["MATH101"]["course"].id,
    )

    isolated_pay_mock.set_payment_status(
        seeded_student.id, cs_catalog["CS101"]["course"].id, paid=True,
    )
    isolated_pay_mock.set_payment_status(
        seeded_student.id, cs_catalog["MATH101"]["course"].id, paid=True,
    )

    finalised, compliance = await reg_service.submit(
        reg.id, student_user_id=uuid.uuid4(),
    )
    assert finalised.status == RegistrationStatus.REGISTERED
    assert compliance["overall_passed"] is True


async def test_payment_missing_lands_in_PAYMENT_HOLD(
    reg_service, open_term, seeded_student, cs_catalog,
):
    """Submit without paying any of the chosen courses."""
    reg = await reg_service.create_draft(
        seeded_student.id, open_term.id, SponsorshipType.SELF_SPONSORED,
    )
    await reg_service.add_course_to_draft(
        reg.id, cs_catalog["CS101"]["course"].id,
    )

    finalised, compliance = await reg_service.submit(
        reg.id, student_user_id=uuid.uuid4(),
    )
    assert finalised.status == RegistrationStatus.PAYMENT_HOLD
    assert compliance["payment_result"]["passed"] is False


# ── Flow 2: Schedule generation populates timetable ─────────────


async def test_schedule_generation_populates_student_timetable(
    async_session, reg_service, sched_service, isolated_pay_mock,
    open_term, seeded_student, cs_catalog,
):
    # Get the student to REGISTERED first.
    reg = await reg_service.create_draft(
        seeded_student.id, open_term.id, SponsorshipType.SELF_SPONSORED,
    )
    await reg_service.add_course_to_draft(
        reg.id, cs_catalog["CS101"]["course"].id,
    )
    isolated_pay_mock.set_payment_status(
        seeded_student.id, cs_catalog["CS101"]["course"].id, paid=True,
    )
    await reg_service.submit(reg.id, student_user_id=uuid.uuid4())

    # Before generation: timetable empty (section_id null)
    before = await sched_service.get_student_timetable(
        seeded_student.id, open_term.id,
    )
    assert before == []

    # Officer triggers schedule generation
    await sched_service.generate_schedule(
        term_id=open_term.id,
        department="Computer Science",
        officer_role=UserRole.REGISTRAR_OFFICER,
        officer_id=uuid.uuid4(),
    )

    # After: the student sees their CS101 section
    after = await sched_service.get_student_timetable(
        seeded_student.id, open_term.id,
    )
    assert len(after) == 1
    assert after[0]["course_code"] == "CS101"


# ── Flow 3: Add/drop after registration ─────────────────────────


async def test_add_then_drop_round_trip(
    async_session, reg_service, add_drop_service, isolated_pay_mock,
    open_term, seeded_student, cs_catalog,
):
    """Reach REGISTERED at 12 ECTS, add a course (16), drop it back (12)."""
    # Step 1 — get to REGISTERED with 12 ECTS (CS101 + MATH101 + CS201
    # would normally need CS101 done; instead pre-pay CS101+MATH101+
    # one extra dummy 4-credit course).
    extra = Course(code="CS199", title="Topics", credit_hours=4,
                   semester=1, department="Computer Science")
    async_session.add(extra)
    await async_session.flush()
    extra_offering = CourseOffering(
        course_id=extra.id, term_id=open_term.id,
        capacity=30, section_count=1,
    )
    async_session.add(extra_offering)
    await async_session.flush()
    extra_section = Section(
        offering_id=extra_offering.id, section_code="A",
        room="NB-999", time_slot="FRI 13:30-15:30",
        capacity=30, enrolled_count=0,
    )
    async_session.add(extra_section)
    await async_session.flush()

    reg = await reg_service.create_draft(
        seeded_student.id, open_term.id, SponsorshipType.SELF_SPONSORED,
    )
    for code in ("CS101", "MATH101", "CS199"):
        course = (
            cs_catalog[code]["course"] if code in cs_catalog else extra
        )
        await reg_service.add_course_to_draft(reg.id, course.id)
        isolated_pay_mock.set_payment_status(
            seeded_student.id, course.id, paid=True,
        )

    finalised, _ = await reg_service.submit(
        reg.id, student_user_id=uuid.uuid4(),
    )
    assert finalised.status == RegistrationStatus.REGISTERED

    # Step 2 — file an ADD for a 4-credit course (load 12 -> 16 ECTS).
    add_target = Course(code="CS150", title="Optional", credit_hours=4,
                        semester=1, department="Computer Science")
    async_session.add(add_target)
    await async_session.flush()
    add_offering = CourseOffering(
        course_id=add_target.id, term_id=open_term.id,
        capacity=30, section_count=1,
    )
    async_session.add(add_offering)
    await async_session.flush()
    add_section = Section(
        offering_id=add_offering.id, section_code="A",
        room="NB-901", time_slot="MON 15:30-17:00",
        capacity=30, enrolled_count=0,
    )
    async_session.add(add_section)
    await async_session.flush()
    isolated_pay_mock.set_payment_status(
        seeded_student.id, add_target.id, paid=True,
    )

    add_request = await add_drop_service.submit_request(
        registration_id=finalised.id,
        course_id=add_target.id,
        action=AddDropAction.ADD,
        deadline=date(2099, 1, 1),
        student_user_id=uuid.uuid4(),
    )
    assert add_request.status == AddDropRequestStatus.APPLIED

    # Step 3 — drop it back (load 16 -> 12 ECTS, clears the floor).
    drop_request = await add_drop_service.submit_request(
        registration_id=finalised.id,
        course_id=add_target.id,
        action=AddDropAction.DROP,
        deadline=date(2099, 1, 1),
        student_user_id=uuid.uuid4(),
    )
    assert drop_request.status == AddDropRequestStatus.APPLIED


# ── Flow 4: Advisory HIGH-risk -> officer queue -> close ────────


async def test_high_risk_advisory_routes_to_officer_queue_and_closes(
    async_session, advisory_service,
    seeded_student, open_term, cs_catalog,
):
    rec = await advisory_service.evaluate_plan(
        student_id=seeded_student.id,
        term_id=open_term.id,
        proposed_course_ids=[cs_catalog["CS101"]["course"].id],
        cgpa=1.7,         # Warning floor — escalates to HIGH
        completed_course_ids=set(),
    )
    assert rec.risk_status == RiskStatus.HIGH
    assert rec.requires_officer_review is True

    queue = await advisory_service.list_high_risk_open(
        term_id=open_term.id, officer_role=UserRole.REGISTRAR_OFFICER,
    )
    assert rec.id in {r.id for r in queue}

    await advisory_service.close_officer_review(
        recommendation_id=rec.id,
        officer_role=UserRole.REGISTRAR_OFFICER,
        officer_id=uuid.uuid4(),
        review_notes="Met with student; advised lighter load.",
    )

    queue_after = await advisory_service.list_high_risk_open(
        term_id=open_term.id, officer_role=UserRole.REGISTRAR_OFFICER,
    )
    assert rec.id not in {r.id for r in queue_after}


# ── Flow 5: Prerequisite block bounces back to draft ────────────


async def test_prereq_failure_bounces_back_and_student_resubmits(
    async_session, reg_service, isolated_pay_mock,
    open_term, seeded_student, cs_catalog,
):
    """
    Submitting CS201 without CS101 in completed history must fail
    and bounce the registration to REGISTRATION_OPEN. The student
    should be able to fix the draft and resubmit cleanly.
    """
    reg = await reg_service.create_draft(
        seeded_student.id, open_term.id, SponsorshipType.SELF_SPONSORED,
    )
    await reg_service.add_course_to_draft(
        reg.id, cs_catalog["CS201"]["course"].id,
    )
    isolated_pay_mock.set_payment_status(
        seeded_student.id, cs_catalog["CS201"]["course"].id, paid=True,
    )

    with pytest.raises(ComplianceCheckFailedError):
        await reg_service.submit(reg.id, student_user_id=uuid.uuid4())

    bounced = await reg_service.registrations.get(reg.id)
    assert bounced.status == RegistrationStatus.REGISTRATION_OPEN

    # Student fixes the draft: drop CS201, add MATH101, mark paid, resubmit.
    await reg_service.remove_course_from_draft(
        reg.id, cs_catalog["CS201"]["course"].id,
    )
    await reg_service.add_course_to_draft(
        reg.id, cs_catalog["MATH101"]["course"].id,
    )
    isolated_pay_mock.set_payment_status(
        seeded_student.id, cs_catalog["MATH101"]["course"].id, paid=True,
    )

    finalised, _ = await reg_service.submit(
        reg.id, student_user_id=uuid.uuid4(),
    )
    assert finalised.status == RegistrationStatus.REGISTERED
