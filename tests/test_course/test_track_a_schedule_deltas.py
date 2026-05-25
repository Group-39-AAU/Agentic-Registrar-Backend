"""
Track A — per-student schedule deltas (add/drop integration).

Covers the post-add/drop schedule flow:

  - GET /me/schedule excludes slots whose course is dropped.
  - GET /me/schedule lists 'pending_additions' for courses on the
    registration that have neither cohort slots nor delta rows.
  - AcademicSchedulingAgent.propose_options_for_course returns every
    section that offers the course, with conflict flags.
  - SchedulingService.accept_section_for_added_course materialises
    the chosen section's slots as StudentScheduleAddition rows.
  - AddDropService apply path deletes addition rows for any course
    the batch dropped.
"""
from __future__ import annotations

import uuid
from datetime import date, time

import pytest
import pytest_asyncio

from app.modules.course.agents import (
    AcademicSchedulingAgent, EnrollmentAdjustmentAgent,
)
from app.modules.course.exceptions import (
    EntityNotFoundError, InvalidAdjustmentRequestError,
)
from app.modules.course.models import (
    ClassScheduleSlot, Course, Registration, RegistrationCourse,
    Section, StudentScheduleAddition,
)
from app.modules.course.service import AddDropService, SchedulingService
from app.modules.course.services import PayMock
from app.shared.enums import (
    AddDropAction, AddDropBatchStatus, RegistrationStatus, SponsorshipType,
    UserRole,
)


# ── Fixtures ────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def cs_courses(async_session) -> dict[str, Course]:
    """Two even-parity CS courses. Both 3-credit so the batch math works."""
    by_code: dict[str, Course] = {}
    for code, sem in (("CS201", 2), ("CS202", 2)):
        c = Course(
            code=code, title=f"{code} title",
            credit_hours=3, semester=sem,
            department="Computer Science",
        )
        async_session.add(c)
        by_code[code] = c
    await async_session.flush()
    return by_code


@pytest_asyncio.fixture
async def cs_student(async_session, seeded_student):
    """Patch into CS sem 2 (even parity) so the agent's checks pass."""
    seeded_student.department = "Computer Science"
    seeded_student.current_semester = 2
    await async_session.flush()
    return seeded_student


@pytest_asyncio.fixture
async def cs_section_a(async_session, seeded_term, cs_courses):
    """Cohort section A for CS sem 2 with two slots: CS201 Mon 09:30, CS202 Tue 09:30."""
    section = Section(
        term_id=seeded_term.id,
        department="Computer Science",
        semester=2,
        section_code="A",
        capacity=30,
        enrolled_count=0,
    )
    async_session.add(section)
    await async_session.flush()
    async_session.add_all([
        ClassScheduleSlot(
            section_id=section.id,
            course_id=cs_courses["CS201"].id,
            day_of_week="MON",
            start_time=time(9, 30), end_time=time(10, 30),
            room="R1",
        ),
        ClassScheduleSlot(
            section_id=section.id,
            course_id=cs_courses["CS202"].id,
            day_of_week="TUE",
            start_time=time(9, 30), end_time=time(10, 30),
            room="R1",
        ),
    ])
    await async_session.flush()
    return section


@pytest_asyncio.fixture
async def cs_section_b(async_session, seeded_term, cs_courses):
    """
    Sibling section B: CS202 on Wed 11:30 (no conflict with section A's
    schedule). Used as the "alternative section" the student picks
    after dropping/re-adding CS202.
    """
    section = Section(
        term_id=seeded_term.id,
        department="Computer Science",
        semester=2,
        section_code="B",
        capacity=30,
        enrolled_count=0,
    )
    async_session.add(section)
    await async_session.flush()
    async_session.add(ClassScheduleSlot(
        section_id=section.id,
        course_id=cs_courses["CS202"].id,
        day_of_week="WED",
        start_time=time(11, 30), end_time=time(12, 30),
        room="R2",
    ))
    await async_session.flush()
    return section


@pytest_asyncio.fixture
async def cs_section_c_conflicting(async_session, seeded_term, cs_courses):
    """
    Section C has CS202 on Mon 09:30 — direct collision with section
    A's CS201. The conflict flag must surface this.
    """
    section = Section(
        term_id=seeded_term.id,
        department="Computer Science",
        semester=2,
        section_code="C",
        capacity=30,
        enrolled_count=0,
    )
    async_session.add(section)
    await async_session.flush()
    async_session.add(ClassScheduleSlot(
        section_id=section.id,
        course_id=cs_courses["CS202"].id,
        day_of_week="MON",
        start_time=time(9, 30), end_time=time(10, 30),
        room="R3",
    ))
    await async_session.flush()
    return section


@pytest_asyncio.fixture
async def cs_registration(
    async_session, cs_student, seeded_term, cs_courses, cs_section_a,
):
    """Student is REGISTERED in section A with CS201 + CS202."""
    reg = Registration(
        student_id=cs_student.id,
        term_id=seeded_term.id,
        status=RegistrationStatus.REGISTERED,
        sponsorship_type=SponsorshipType.GOVERNMENT,
        section_id=cs_section_a.id,
    )
    async_session.add(reg)
    await async_session.flush()
    async_session.add_all([
        RegistrationCourse(
            registration_id=reg.id,
            course_id=cs_courses["CS201"].id,
            is_dropped=False,
        ),
        RegistrationCourse(
            registration_id=reg.id,
            course_id=cs_courses["CS202"].id,
            is_dropped=False,
        ),
    ])
    await async_session.flush()
    return reg


# ── /me/schedule reader ─────────────────────────────────────────


async def test_schedule_includes_cohort_slots_and_no_pending(
    async_session, cs_student, seeded_term, cs_courses, cs_registration,
):
    svc = SchedulingService(async_session)
    payload = await svc.get_student_schedule(cs_student.id, seeded_term.id)
    codes = {s["course_code"] for s in payload["slots"]}
    assert codes == {"CS201", "CS202"}
    assert all(s["source"] == "cohort" for s in payload["slots"])
    assert payload["pending_additions"] == []


async def test_schedule_excludes_dropped_courses_from_cohort(
    async_session, cs_student, seeded_term, cs_courses, cs_registration,
):
    """Flipping is_dropped should immediately hide the cohort slot."""
    await async_session.refresh(cs_registration, attribute_names=["courses"])
    cs202_link = next(
        rc for rc in cs_registration.courses
        if rc.course_id == cs_courses["CS202"].id
    )
    cs202_link.is_dropped = True
    await async_session.flush()

    svc = SchedulingService(async_session)
    payload = await svc.get_student_schedule(cs_student.id, seeded_term.id)
    codes = {s["course_code"] for s in payload["slots"]}
    assert codes == {"CS201"}


async def test_added_course_with_no_addition_yet_is_pending(
    async_session, cs_student, seeded_term, cs_courses, cs_registration,
):
    """
    Add a course that the cohort section doesn't run — until the
    student picks a section, it should surface as pending.
    """
    new_course = Course(
        code="CS210", title="CS 210",
        credit_hours=3, semester=2, department="Computer Science",
    )
    async_session.add(new_course)
    await async_session.flush()
    async_session.add(RegistrationCourse(
        registration_id=cs_registration.id,
        course_id=new_course.id,
        is_dropped=False,
    ))
    await async_session.flush()

    svc = SchedulingService(async_session)
    payload = await svc.get_student_schedule(cs_student.id, seeded_term.id)
    pending_codes = {p["course_code"] for p in payload["pending_additions"]}
    assert pending_codes == {"CS210"}


async def test_schedule_includes_addition_slots(
    async_session, cs_student, seeded_term,
    cs_courses, cs_registration, cs_section_b,
):
    """
    Once an addition row exists, /me/schedule surfaces the slot as
    source='addition' alongside the cohort rows.
    """
    cs202_alt_slot = (
        await async_session.execute(
            __import__("sqlalchemy").select(ClassScheduleSlot).where(
                ClassScheduleSlot.section_id == cs_section_b.id,
            )
        )
    ).scalar_one()
    async_session.add(StudentScheduleAddition(
        registration_id=cs_registration.id,
        schedule_slot_id=cs202_alt_slot.id,
        course_id=cs_courses["CS202"].id,
        source_section_id=cs_section_b.id,
    ))
    await async_session.flush()

    svc = SchedulingService(async_session)
    payload = await svc.get_student_schedule(cs_student.id, seeded_term.id)
    sources = {(s["course_code"], s["source"]) for s in payload["slots"]}
    # CS201 stays cohort; CS202 has BOTH cohort + addition (pre-drop scenario).
    assert ("CS201", "cohort") in sources
    assert ("CS202", "cohort") in sources
    assert ("CS202", "addition") in sources


# ── Agent: propose_options_for_course ──────────────────────────


async def test_propose_options_lists_each_section_with_conflict_flag(
    async_session, cs_student, seeded_term, cs_courses,
    cs_registration, cs_section_b, cs_section_c_conflicting,
):
    """
    For CS202 — three sections offer it (A=cohort already, B=Wed 11:30
    no conflict, C=Mon 09:30 collides with cohort CS201). All three
    appear; A and B are viable, C flagged is_viable=False.
    """
    agent = AcademicSchedulingAgent()
    options = await agent.propose_options_for_course(
        async_session,
        registration=cs_registration,
        course_id=cs_courses["CS202"].id,
    )
    by_code = {o["section_code"]: o for o in options}
    assert set(by_code) == {"A", "B", "C"}
    assert by_code["A"]["is_viable"] is True
    assert by_code["B"]["is_viable"] is True
    assert by_code["C"]["is_viable"] is False
    # The conflict mentions CS201 (Mon 09:30) as the collision.
    assert any(
        c["collides_with"]["course_code"] == "CS201"
        for c in by_code["C"]["conflicts"]
    )


async def test_propose_options_returns_empty_for_unknown_course(
    async_session, cs_student, seeded_term, cs_registration,
):
    agent = AcademicSchedulingAgent()
    fake_id = uuid.UUID("00000000-0000-0000-0000-000000000999")
    options = await agent.propose_options_for_course(
        async_session,
        registration=cs_registration,
        course_id=fake_id,
    )
    assert options == []


# ── Agent: accept_section_for_course ───────────────────────────


async def test_accept_writes_addition_rows(
    async_session, cs_student, seeded_term, cs_courses,
    cs_registration, cs_section_b,
):
    agent = AcademicSchedulingAgent()
    created = await agent.accept_section_for_course(
        async_session,
        registration=cs_registration,
        course_id=cs_courses["CS202"].id,
        section_id=cs_section_b.id,
    )
    assert len(created) == 1
    assert created[0].course_id == cs_courses["CS202"].id
    assert created[0].source_section_id == cs_section_b.id


async def test_accept_is_idempotent(
    async_session, cs_student, seeded_term, cs_courses,
    cs_registration, cs_section_b,
):
    agent = AcademicSchedulingAgent()
    await agent.accept_section_for_course(
        async_session,
        registration=cs_registration,
        course_id=cs_courses["CS202"].id,
        section_id=cs_section_b.id,
    )
    await async_session.flush()
    second = await agent.accept_section_for_course(
        async_session,
        registration=cs_registration,
        course_id=cs_courses["CS202"].id,
        section_id=cs_section_b.id,
    )
    assert second == []      # nothing new — already there


# ── SchedulingService accept guard ─────────────────────────────


async def test_service_accept_rejects_non_viable_section(
    async_session, cs_student, seeded_term, cs_courses,
    cs_registration, cs_section_c_conflicting,
):
    """A stale client posting the conflicting section gets 409."""
    svc = SchedulingService(async_session)
    with pytest.raises(InvalidAdjustmentRequestError):
        await svc.accept_section_for_added_course(
            student_id=cs_student.id,
            course_id=cs_courses["CS202"].id,
            section_id=cs_section_c_conflicting.id,
        )


async def test_service_accept_rejects_course_not_on_registration(
    async_session, cs_student, seeded_term, cs_section_b,
):
    """Posting a course the student hasn't added → 404."""
    other = Course(
        code="OTHER", title="Other", credit_hours=3, semester=2,
        department="Computer Science",
    )
    async_session.add(other)
    await async_session.flush()
    svc = SchedulingService(async_session)
    with pytest.raises(EntityNotFoundError):
        await svc.accept_section_for_added_course(
            student_id=cs_student.id,
            course_id=other.id,
            section_id=cs_section_b.id,
        )


# ── Add/Drop apply path cleans up additions on DROP ───────────


async def test_drop_apply_removes_existing_schedule_additions(
    async_session, cs_student, seeded_term, cs_courses,
    cs_registration, cs_section_b,
):
    """
    Setup: student added CS202 from section B (addition row exists).
    Action: officer applies a batch that DROPs CS202.
    Expected: the StudentScheduleAddition row for CS202 is deleted.
    """
    # Pre-existing addition for CS202 from section B
    cs202_alt_slot = (
        await async_session.execute(
            __import__("sqlalchemy").select(ClassScheduleSlot).where(
                ClassScheduleSlot.section_id == cs_section_b.id,
            )
        )
    ).scalar_one()
    async_session.add(StudentScheduleAddition(
        registration_id=cs_registration.id,
        schedule_slot_id=cs202_alt_slot.id,
        course_id=cs_courses["CS202"].id,
        source_section_id=cs_section_b.id,
    ))
    # Pad the load so the DROP keeps total >= 12 ECTS.
    pad = Course(
        code="CSPAD", title="Pad", credit_hours=12, semester=2,
        department="Computer Science",
    )
    async_session.add(pad)
    await async_session.flush()
    async_session.add(RegistrationCourse(
        registration_id=cs_registration.id,
        course_id=pad.id,
        is_dropped=False,
    ))
    await async_session.flush()

    class _AlwaysPaid(PayMock):
        def get_payment_status(self, *_a, **_kw):
            return True

    agent = EnrollmentAdjustmentAgent(payment_service=_AlwaysPaid())
    svc = AddDropService(async_session, adjustment_agent=agent)

    # Officer fixture also implicitly creates a User; we can fake by
    # using the student's user_id since the service only stores the
    # officer_id (no FK validation in the test session).
    from sqlalchemy import select as _select
    batch = await svc.submit_batch(
        registration_id=cs_registration.id,
        items=[(cs_courses["CS202"].id, AddDropAction.DROP)],
        student_user_id=cs_student.user_id,
        deadline=date(2099, 1, 1),
    )
    assert batch.status == AddDropBatchStatus.AGENT_APPROVED

    # Build a DEPARTMENT_HEAD officer for the student's department so
    # the add/drop auth gate (DH-scoped) accepts the approval.
    from app.modules.auth.models import User
    from app.modules.course.models import CourseManagementOfficer
    from app.shared.enums import OfficerRole
    dh_user = User(
        id=uuid.uuid4(),
        email="dh-deltas@aau.edu.et",
        first_name="DH",
        last_name="Deltas",
        hashed_password="not-a-real-hash",
        role=UserRole.REGISTRAR_OFFICER,
        is_active=True,
    )
    async_session.add(dh_user)
    await async_session.flush()
    async_session.add(CourseManagementOfficer(
        user_id=dh_user.id,
        staff_id="DH/DELTAS/01",
        role=OfficerRole.DEPARTMENT_HEAD,
        department=cs_student.department,
        authorization_level=5,
    ))
    await async_session.flush()

    applied = await svc.officer_approve_batch(
        batch.id,
        user_id=dh_user.id,
    )
    assert applied.status == AddDropBatchStatus.APPLIED

    # The addition row for CS202 should be gone.
    leftover = (
        await async_session.execute(
            __import__("sqlalchemy").select(StudentScheduleAddition).where(
                StudentScheduleAddition.registration_id == cs_registration.id,
                StudentScheduleAddition.course_id == cs_courses["CS202"].id,
            )
        )
    ).scalars().all()
    assert leftover == []
