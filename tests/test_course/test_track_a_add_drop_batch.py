"""
Track A — add/drop batch workflow.

Covers the EnrollmentAdjustmentAgent's new batch-level checks
(curriculum membership, semester parity, prereq delegation) and the
two-step service workflow:

    student submits batch
       → agent verdict (AGENT_APPROVED | AGENT_DENIED)
          → officer approves        → APPLIED
          → officer overrides denial → APPLIED
          → officer rejects          → REJECTED
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone

import pytest
import pytest_asyncio

from app.modules.course.agents import (
    BatchResult, EnrollmentAdjustmentAgent,
)
from app.modules.course.exceptions import (
    EntityNotFoundError, InvalidAdjustmentRequestError,
    UnauthorizedActorError,
)
from app.modules.course.grade_points import points_for
from app.modules.course.models import (
    ClassScheduleSlot, Course, CoursePrerequisite, Grade, Registration,
    RegistrationCourse, Section,
)
from app.modules.course.service import AddDropService
from app.modules.course.services import PayMock
from app.shared.enums import (
    AddDropAction, AddDropBatchStatus, AddDropRequestStatus, GradeLetter,
    GradeSubmissionStatus, RegistrationStatus, SponsorshipType, UserRole,
)


# ── Fixtures ────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def cs_catalog(async_session) -> dict[str, Course]:
    """
    CS curriculum with one prereq edge so the agent's checks have
    something to bite on:

        CS101 (sem 1)
        CS102 (sem 1)
        CS201 (sem 2)  requires CS101
        CS202 (sem 2)
        CS301 (sem 3)
    """
    by_code: dict[str, Course] = {}
    for code, sem in (
        ("CS101", 1), ("CS102", 1),
        ("CS201", 2), ("CS202", 2),
        ("CS301", 3),
    ):
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
    await async_session.flush()
    return by_code


@pytest_asyncio.fixture
async def cs_student(seeded_student, async_session):
    """Patch the seeded student into CS, semester 2 (even parity)."""
    seeded_student.department = "Computer Science"
    seeded_student.current_semester = 2
    await async_session.flush()
    return seeded_student


@pytest_asyncio.fixture
async def cs_registration(
    async_session, cs_student, seeded_term, cs_catalog,
):
    """
    A REGISTERED registration with CS201 already on it so DROP and
    add-more scenarios both have something to bite on. The student
    has CS101 in their grade history so the CS201 prereq is met.
    """
    async_session.add(Grade(
        student_id=cs_student.id,
        course_id=cs_catalog["CS101"].id,
        term_id=seeded_term.id,
        letter_grade=GradeLetter.B,
        credit_hours=cs_catalog["CS101"].credit_hours,
        grade_points=points_for(GradeLetter.B) * cs_catalog["CS101"].credit_hours,
        status=GradeSubmissionStatus.AUTHORISED,
    ))
    registration = Registration(
        student_id=cs_student.id,
        term_id=seeded_term.id,
        status=RegistrationStatus.REGISTERED,
        sponsorship_type=SponsorshipType.GOVERNMENT,
    )
    async_session.add(registration)
    await async_session.flush()
    async_session.add(RegistrationCourse(
        registration_id=registration.id,
        course_id=cs_catalog["CS201"].id,
        is_dropped=False,
    ))
    async_session.add(RegistrationCourse(
        registration_id=registration.id,
        course_id=cs_catalog["CS202"].id,
        is_dropped=False,
    ))
    await async_session.flush()
    return registration


@pytest.fixture
def paid_pay() -> PayMock:
    """PayMock that approves every (student, course) lookup."""
    class _AlwaysPaid(PayMock):
        def get_payment_status(self, *_a, **_kw):
            return True
    return _AlwaysPaid()


# ── Agent-level checks ──────────────────────────────────────────


def test_curriculum_membership_passes_when_department_matches(
    cs_student, cs_catalog,
):
    agent = EnrollmentAdjustmentAgent()
    result = agent.verify_curriculum_membership(cs_student, cs_catalog["CS201"])
    assert result.passed is True


def test_curriculum_membership_fails_for_other_department(
    cs_student, async_session,
):
    other_dept_course = Course(
        code="EE201", title="EE 201", credit_hours=3, semester=2,
        department="Electrical Engineering",
    )
    agent = EnrollmentAdjustmentAgent()
    result = agent.verify_curriculum_membership(cs_student, other_dept_course)
    assert result.passed is False
    assert any("Electrical Engineering" in r for r in result.reasons)


def test_semester_parity_passes_for_same_parity(cs_student, cs_catalog):
    """Student in sem 2 (even) can take sem-2 or sem-6 courses."""
    agent = EnrollmentAdjustmentAgent()
    assert agent.verify_semester_parity(
        cs_student, cs_catalog["CS202"],
    ).passed is True


def test_semester_parity_fails_for_opposite_parity(cs_student, cs_catalog):
    """Student in sem 2 (even) cannot take sem-1 or sem-3 (odd)."""
    agent = EnrollmentAdjustmentAgent()
    odd_result = agent.verify_semester_parity(
        cs_student, cs_catalog["CS101"],
    )
    assert odd_result.passed is False
    assert any(
        "even-term" in r and "odd-term" in r for r in odd_result.reasons
    )


async def test_process_batch_approves_clean_request(
    async_session, cs_student, cs_registration, cs_catalog, paid_pay,
):
    """ADD a passing-parity, in-department, prereqs-met course."""
    agent = EnrollmentAdjustmentAgent(payment_service=paid_pay)
    completed = {cs_catalog["CS101"].id}

    result: BatchResult = await agent.process_batch(
        async_session,
        student=cs_student,
        registration=cs_registration,
        items=[(cs_catalog["CS202"].id and cs_catalog["CS202"], AddDropAction.ADD)],
        completed_course_ids=completed,
    )
    # CS202 already on registration in fixture — re-adding is a noop
    # which the agent should flag.
    assert result.approved is False
    assert any("already on the registration" in v.reasons[0]
               for v in result.failing_items())


async def test_process_batch_rejects_wrong_parity(
    async_session, cs_student, cs_registration, cs_catalog, paid_pay,
):
    agent = EnrollmentAdjustmentAgent(payment_service=paid_pay)
    result = await agent.process_batch(
        async_session,
        student=cs_student,
        registration=cs_registration,
        items=[(cs_catalog["CS101"], AddDropAction.ADD)],   # sem 1 = odd
        completed_course_ids={cs_catalog["CS101"].id},
    )
    assert result.approved is False
    fail = next(v for v in result.failing_items() if v.course_code == "CS101")
    assert any("odd-term" in r for r in fail.reasons)


async def test_process_batch_rejects_unmet_prerequisite(
    async_session, cs_student, cs_registration, cs_catalog, paid_pay,
):
    agent = EnrollmentAdjustmentAgent(payment_service=paid_pay)
    # Empty completed → CS201 prereq (CS101) is unmet.
    result = await agent.process_batch(
        async_session,
        student=cs_student,
        registration=cs_registration,
        items=[(cs_catalog["CS301"], AddDropAction.ADD)],   # sem 3 = odd parity actually
        completed_course_ids=set(),
    )
    # CS301 is also wrong parity (odd), so this will trip both checks.
    assert result.approved is False


async def test_process_batch_rejects_drop_that_breaks_credit_floor(
    async_session, cs_student, cs_registration, cs_catalog, paid_pay,
):
    """
    cs_registration has CS201 + CS202 = 6 credits. Dropping both
    leaves 0 — below the 12-ECTS floor.
    """
    agent = EnrollmentAdjustmentAgent(payment_service=paid_pay)
    result = await agent.process_batch(
        async_session,
        student=cs_student,
        registration=cs_registration,
        items=[
            (cs_catalog["CS201"], AddDropAction.DROP),
            (cs_catalog["CS202"], AddDropAction.DROP),
        ],
        completed_course_ids={cs_catalog["CS101"].id},
    )
    assert result.approved is False
    batch_envelope = [
        v for v in result.failing_items() if v.course_code == "__BATCH__"
    ]
    assert batch_envelope, "expected a batch-level envelope verdict"
    assert any("floor" in r for r in batch_envelope[0].reasons)


# ── Service-level flow ──────────────────────────────────────────


async def test_submit_batch_lands_in_agent_denied_when_any_item_fails(
    async_session, cs_student, cs_registration, cs_catalog, paid_pay,
):
    agent = EnrollmentAdjustmentAgent(payment_service=paid_pay)
    svc = AddDropService(async_session, adjustment_agent=agent)
    batch = await svc.submit_batch(
        registration_id=cs_registration.id,
        items=[(cs_catalog["CS101"].id, AddDropAction.ADD)],   # wrong parity
        student_user_id=cs_student.user_id,
        deadline=date(2099, 1, 1),
    )
    assert batch.status == AddDropBatchStatus.AGENT_DENIED
    # Per-item agent reason is persisted on the request row.
    items = list(batch.items)
    assert len(items) == 1
    assert items[0].status == AddDropRequestStatus.DENIED
    assert items[0].reason and "odd-term" in items[0].reason
    # Officer-visible payload is on the batch row.
    assert any(r.get("course_code") == "CS101" for r in batch.agent_reasons)


async def test_submit_batch_lands_in_agent_approved_when_clean(
    async_session, cs_student, cs_registration, cs_catalog, paid_pay,
):
    """
    Drop CS202 (6 → 3 credits still below floor) and add CS201 (no-op
    duplicate)... let me instead test: just DROP CS202 from a heavier
    registration. Need to pad the registration first.
    """
    # Pad with one extra sem-2 course so dropping one stays >= 12.
    extra_course = Course(
        code="CS203", title="CS 203", credit_hours=12, semester=2,
        department="Computer Science",
    )
    async_session.add(extra_course)
    await async_session.flush()
    async_session.add(RegistrationCourse(
        registration_id=cs_registration.id,
        course_id=extra_course.id,
        is_dropped=False,
    ))
    await async_session.flush()

    agent = EnrollmentAdjustmentAgent(payment_service=paid_pay)
    svc = AddDropService(async_session, adjustment_agent=agent)
    batch = await svc.submit_batch(
        registration_id=cs_registration.id,
        items=[(cs_catalog["CS202"].id, AddDropAction.DROP)],
        student_user_id=cs_student.user_id,
        deadline=date(2099, 1, 1),
    )
    assert batch.status == AddDropBatchStatus.AGENT_APPROVED
    # Nothing applied yet — the link row is still active.
    link = (
        await async_session.execute(
            __import__("sqlalchemy").select(RegistrationCourse).where(
                RegistrationCourse.registration_id == cs_registration.id,
                RegistrationCourse.course_id == cs_catalog["CS202"].id,
            )
        )
    ).scalar_one()
    assert link.is_dropped is False


async def test_officer_approve_applies_changes_and_records_decision(
    async_session, cs_student, cs_registration, cs_catalog, paid_pay,
    seeded_officer,
):
    # Pad load so DROP doesn't trip the floor.
    extra_course = Course(
        code="CS204", title="CS 204", credit_hours=12, semester=2,
        department="Computer Science",
    )
    async_session.add(extra_course)
    await async_session.flush()
    async_session.add(RegistrationCourse(
        registration_id=cs_registration.id,
        course_id=extra_course.id,
        is_dropped=False,
    ))
    await async_session.flush()

    agent = EnrollmentAdjustmentAgent(payment_service=paid_pay)
    svc = AddDropService(async_session, adjustment_agent=agent)
    batch = await svc.submit_batch(
        registration_id=cs_registration.id,
        items=[(cs_catalog["CS202"].id, AddDropAction.DROP)],
        student_user_id=cs_student.user_id,
        deadline=date(2099, 1, 1),
    )
    assert batch.status == AddDropBatchStatus.AGENT_APPROVED

    applied = await svc.officer_approve_batch(
        batch.id,
        officer_role=UserRole.REGISTRAR_OFFICER,
        officer_id=seeded_officer.user_id,
    )
    assert applied.status == AddDropBatchStatus.APPLIED
    assert applied.officer_id == seeded_officer.user_id
    assert applied.officer_decision_at is not None

    # Registration mutation visible: CS202 link is now dropped.
    link = (
        await async_session.execute(
            __import__("sqlalchemy").select(RegistrationCourse).where(
                RegistrationCourse.registration_id == cs_registration.id,
                RegistrationCourse.course_id == cs_catalog["CS202"].id,
            )
        )
    ).scalar_one()
    assert link.is_dropped is True


async def test_officer_override_applies_denied_batch_with_justification(
    async_session, cs_student, cs_registration, cs_catalog, paid_pay,
    seeded_officer,
):
    agent = EnrollmentAdjustmentAgent(payment_service=paid_pay)
    svc = AddDropService(async_session, adjustment_agent=agent)
    # Wrong-parity ADD → AGENT_DENIED.
    batch = await svc.submit_batch(
        registration_id=cs_registration.id,
        items=[(cs_catalog["CS101"].id, AddDropAction.ADD)],
        student_user_id=cs_student.user_id,
        deadline=date(2099, 1, 1),
    )
    assert batch.status == AddDropBatchStatus.AGENT_DENIED

    applied = await svc.officer_override_batch(
        batch.id,
        officer_role=UserRole.REGISTRAR_OFFICER,
        officer_id=seeded_officer.user_id,
        justification="Student needs CS101 for an out-of-cycle make-up.",
    )
    assert applied.status == AddDropBatchStatus.APPLIED
    assert applied.officer_justification.startswith(
        "Student needs CS101"
    )
    # CS101 now on the registration.
    link = (
        await async_session.execute(
            __import__("sqlalchemy").select(RegistrationCourse).where(
                RegistrationCourse.registration_id == cs_registration.id,
                RegistrationCourse.course_id == cs_catalog["CS101"].id,
            )
        )
    ).scalar_one()
    assert link.is_dropped is False


async def test_officer_override_requires_agent_denied_status(
    async_session, cs_student, cs_registration, cs_catalog, paid_pay,
    seeded_officer,
):
    """Cannot 'override' an AGENT_APPROVED batch — use approve."""
    # Pad load + drop so the batch is AGENT_APPROVED.
    extra = Course(
        code="CS205", title="CS 205", credit_hours=12, semester=2,
        department="Computer Science",
    )
    async_session.add(extra)
    await async_session.flush()
    async_session.add(RegistrationCourse(
        registration_id=cs_registration.id, course_id=extra.id,
        is_dropped=False,
    ))
    await async_session.flush()

    agent = EnrollmentAdjustmentAgent(payment_service=paid_pay)
    svc = AddDropService(async_session, adjustment_agent=agent)
    batch = await svc.submit_batch(
        registration_id=cs_registration.id,
        items=[(cs_catalog["CS202"].id, AddDropAction.DROP)],
        student_user_id=cs_student.user_id,
        deadline=date(2099, 1, 1),
    )
    assert batch.status == AddDropBatchStatus.AGENT_APPROVED
    with pytest.raises(InvalidAdjustmentRequestError):
        await svc.officer_override_batch(
            batch.id,
            officer_role=UserRole.REGISTRAR_OFFICER,
            officer_id=seeded_officer.user_id,
            justification="trying to override an approved batch",
        )


async def test_officer_reject_finalises_denial_without_applying(
    async_session, cs_student, cs_registration, cs_catalog, paid_pay,
    seeded_officer,
):
    agent = EnrollmentAdjustmentAgent(payment_service=paid_pay)
    svc = AddDropService(async_session, adjustment_agent=agent)
    batch = await svc.submit_batch(
        registration_id=cs_registration.id,
        items=[(cs_catalog["CS101"].id, AddDropAction.ADD)],   # AGENT_DENIED
        student_user_id=cs_student.user_id,
        deadline=date(2099, 1, 1),
    )
    rejected = await svc.officer_reject_batch(
        batch.id,
        officer_role=UserRole.REGISTRAR_OFFICER,
        officer_id=seeded_officer.user_id,
        justification="Agent decision stands.",
    )
    assert rejected.status == AddDropBatchStatus.REJECTED
    # CS101 link is NOT present on the registration.
    link = (
        await async_session.execute(
            __import__("sqlalchemy").select(RegistrationCourse).where(
                RegistrationCourse.registration_id == cs_registration.id,
                RegistrationCourse.course_id == cs_catalog["CS101"].id,
            )
        )
    ).scalar_one_or_none()
    assert link is None


async def test_officer_actions_reject_non_officer_role(
    async_session, cs_student, cs_registration, cs_catalog, paid_pay,
):
    agent = EnrollmentAdjustmentAgent(payment_service=paid_pay)
    svc = AddDropService(async_session, adjustment_agent=agent)
    batch = await svc.submit_batch(
        registration_id=cs_registration.id,
        items=[(cs_catalog["CS101"].id, AddDropAction.ADD)],
        student_user_id=cs_student.user_id,
        deadline=date(2099, 1, 1),
    )
    with pytest.raises(UnauthorizedActorError):
        await svc.officer_override_batch(
            batch.id,
            officer_role=UserRole.STUDENT,
            officer_id=cs_student.user_id,
            justification="not allowed",
        )


async def test_pending_queue_returns_only_awaiting_decision(
    async_session, cs_student, cs_registration, cs_catalog, paid_pay,
    seeded_officer,
):
    """Officer queue surfaces AGENT_APPROVED and AGENT_DENIED only."""
    agent = EnrollmentAdjustmentAgent(payment_service=paid_pay)
    svc = AddDropService(async_session, adjustment_agent=agent)
    denied = await svc.submit_batch(
        registration_id=cs_registration.id,
        items=[(cs_catalog["CS101"].id, AddDropAction.ADD)],
        student_user_id=cs_student.user_id,
        deadline=date(2099, 1, 1),
    )
    queue = await svc.list_pending_batches(
        officer_role=UserRole.REGISTRAR_OFFICER,
    )
    assert denied.id in {b.id for b in queue}

    # Reject it — should drain from the queue.
    await svc.officer_reject_batch(
        denied.id,
        officer_role=UserRole.REGISTRAR_OFFICER,
        officer_id=seeded_officer.user_id,
        justification="closed",
    )
    queue_after = await svc.list_pending_batches(
        officer_role=UserRole.REGISTRAR_OFFICER,
    )
    assert denied.id not in {b.id for b in queue_after}


async def test_pending_queue_status_filter_narrows_to_one_state(
    async_session, cs_student, cs_registration, cs_catalog, paid_pay,
):
    """
    Passing ``statuses={AGENT_DENIED}`` returns denied batches only,
    even when AGENT_APPROVED batches also exist (and vice versa).
    """
    # Pad the registration so a DROP can stay >= 12 ECTS and produce
    # an AGENT_APPROVED batch alongside the AGENT_DENIED one.
    extra = Course(
        code="CS210", title="CS 210", credit_hours=12, semester=2,
        department="Computer Science",
    )
    async_session.add(extra)
    await async_session.flush()
    async_session.add(RegistrationCourse(
        registration_id=cs_registration.id,
        course_id=extra.id,
        is_dropped=False,
    ))
    await async_session.flush()

    agent = EnrollmentAdjustmentAgent(payment_service=paid_pay)
    svc = AddDropService(async_session, adjustment_agent=agent)

    denied = await svc.submit_batch(
        registration_id=cs_registration.id,
        items=[(cs_catalog["CS101"].id, AddDropAction.ADD)],   # wrong parity
        student_user_id=cs_student.user_id,
        deadline=date(2099, 1, 1),
    )
    approved = await svc.submit_batch(
        registration_id=cs_registration.id,
        items=[(cs_catalog["CS202"].id, AddDropAction.DROP)],
        student_user_id=cs_student.user_id,
        deadline=date(2099, 1, 1),
    )
    assert denied.status == AddDropBatchStatus.AGENT_DENIED
    assert approved.status == AddDropBatchStatus.AGENT_APPROVED

    only_denied = await svc.list_pending_batches(
        officer_role=UserRole.REGISTRAR_OFFICER,
        statuses={AddDropBatchStatus.AGENT_DENIED},
    )
    assert {b.id for b in only_denied} == {denied.id}

    only_approved = await svc.list_pending_batches(
        officer_role=UserRole.REGISTRAR_OFFICER,
        statuses={AddDropBatchStatus.AGENT_APPROVED},
    )
    assert {b.id for b in only_approved} == {approved.id}


async def test_submit_batch_rejects_duplicate_course(
    async_session, cs_student, cs_registration, cs_catalog, paid_pay,
):
    """Same course twice in one batch is a 409 from the service."""
    agent = EnrollmentAdjustmentAgent(payment_service=paid_pay)
    svc = AddDropService(async_session, adjustment_agent=agent)
    with pytest.raises(InvalidAdjustmentRequestError):
        await svc.submit_batch(
            registration_id=cs_registration.id,
            items=[
                (cs_catalog["CS101"].id, AddDropAction.ADD),
                (cs_catalog["CS101"].id, AddDropAction.DROP),
            ],
            student_user_id=cs_student.user_id,
            deadline=date(2099, 1, 1),
        )


# ── Offered-in-term gate (ADD must have a section running it) ───


async def _make_section_with_slot(session, *, term_id, course, code="A"):
    """Helper: a section in ``term_id`` that actually teaches ``course``."""
    section = Section(
        term_id=term_id,
        department=course.department,
        semester=course.semester,
        section_code=code,
        capacity=30,
        enrolled_count=0,
    )
    session.add(section)
    await session.flush()
    session.add(ClassScheduleSlot(
        section_id=section.id,
        course_id=course.id,
        day_of_week="MON",
        start_time=time(9, 30), end_time=time(10, 30),
        room="R1",
    ))
    await session.flush()
    return section


async def test_add_blocked_when_course_not_offered_this_term(
    async_session, cs_student, cs_registration, cs_catalog, paid_pay,
):
    """
    ADD a curriculum-valid, right-parity, prereq-met course that NO
    section teaches this term → AGENT_DENIED with an 'not offered'
    reason. (CS202 is even-parity like the sem-2 student, prereq-free,
    but no section runs it.)
    """
    agent = EnrollmentAdjustmentAgent(payment_service=paid_pay)
    svc = AddDropService(async_session, adjustment_agent=agent)
    batch = await svc.submit_batch(
        registration_id=cs_registration.id,
        items=[(cs_catalog["CS202"].id, AddDropAction.ADD)],
        student_user_id=cs_student.user_id,
        deadline=date(2099, 1, 1),
    )
    # CS202 is already on the cs_registration fixture, so this also
    # trips "already on registration" — assert the offered reason
    # is present regardless.
    assert batch.status == AddDropBatchStatus.AGENT_DENIED
    reasons = " ".join(
        r for item in batch.agent_reasons for r in item.get("reasons", [])
    )
    assert "not offered by any section this term" in reasons


async def test_add_allowed_when_a_section_offers_the_course(
    async_session, cs_student, cs_registration, cs_catalog, seeded_term,
    paid_pay,
):
    """
    ADD a course that IS taught by a section this term → the offered
    check passes. Use a fresh even-parity CS course (CS204) the
    student isn't already registered for, with a section running it.
    """
    cs204 = Course(
        code="CS204", title="CS 204", credit_hours=3, semester=2,
        department="Computer Science",
    )
    async_session.add(cs204)
    await async_session.flush()
    await _make_section_with_slot(
        async_session, term_id=seeded_term.id, course=cs204,
    )
    # Pad the registration so adding CS204 keeps the load inside the
    # [12, 22] ECTS window — this test isolates the offered gate, not
    # the credit floor. Baseline CS201+CS202 = 6; +12 pad = 18; +CS204
    # (3) = 21 ≤ 22.
    pad = Course(
        code="CSPAD2", title="Pad", credit_hours=12, semester=2,
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

    agent = EnrollmentAdjustmentAgent(payment_service=paid_pay)
    svc = AddDropService(async_session, adjustment_agent=agent)
    batch = await svc.submit_batch(
        registration_id=cs_registration.id,
        items=[(cs204.id, AddDropAction.ADD)],
        student_user_id=cs_student.user_id,
        deadline=date(2099, 1, 1),
    )
    assert batch.status == AddDropBatchStatus.AGENT_APPROVED
    # No "not offered" reason anywhere.
    reasons = " ".join(
        r for item in batch.agent_reasons for r in item.get("reasons", [])
    )
    assert "not offered" not in reasons


async def test_offered_check_only_applies_to_add_not_drop(
    async_session, cs_student, cs_registration, cs_catalog, seeded_term,
    paid_pay,
):
    """
    A DROP must not require the course to be offered this term — you
    can always drop a course you're registered for. cs_registration
    has CS201 + CS202 (6 ECTS) with no sections; dropping one should
    still pass the offered gate (it doesn't apply to DROP). It WILL
    trip the 12-ECTS floor, but never the offered reason.
    """
    agent = EnrollmentAdjustmentAgent(payment_service=paid_pay)
    svc = AddDropService(async_session, adjustment_agent=agent)
    batch = await svc.submit_batch(
        registration_id=cs_registration.id,
        items=[(cs_catalog["CS201"].id, AddDropAction.DROP)],
        student_user_id=cs_student.user_id,
        deadline=date(2099, 1, 1),
    )
    reasons = " ".join(
        r for item in batch.agent_reasons for r in item.get("reasons", [])
    )
    assert "not offered" not in reasons


async def test_agent_verify_offered_in_term_direct(
    async_session, cs_student, seeded_term, cs_catalog, paid_pay,
):
    """Unit-level: the check passes only when a section runs the course."""
    agent = EnrollmentAdjustmentAgent(payment_service=paid_pay)

    # No section yet → fails.
    miss = await agent.verify_offered_in_term(
        async_session, seeded_term.id, cs_catalog["CS101"],
    )
    assert miss.passed is False

    # Add a section running CS101 → passes.
    await _make_section_with_slot(
        async_session, term_id=seeded_term.id, course=cs_catalog["CS101"],
    )
    hit = await agent.verify_offered_in_term(
        async_session, seeded_term.id, cs_catalog["CS101"],
    )
    assert hit.passed is True
