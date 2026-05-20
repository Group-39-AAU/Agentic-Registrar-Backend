"""
Track C PR C4 — Unified exception queue tests.

Verifies the join over the three per-source pending queues:
  * ADVISORY  — open HIGH-risk advisory verdicts
  * GRADING   — FLAGGED grade batches
  * STANDING  — held-for-review standing rows

Each test seeds one or more sources and asserts the queue normalises
them into the uniform ``ExceptionQueueEntry`` shape.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.modules.auth.models import User
from app.modules.course.exception_queue.schemas import ExceptionSource
from app.modules.course.exception_queue.service import ExceptionQueueService
from app.modules.course.exceptions import UnauthorizedActorError
from app.modules.course.grading.models import (
    AssessmentBreakdown, AssessmentComponent, GradeBatch,
)
from app.modules.course.models import (
    AcademicTerm, AdvisoryRecommendation, Course, CourseManagementOfficer,
    Instructor, Registration, Section, Student,
)
from app.modules.course.standing.models import AcademicStanding
from app.shared.enums import (
    AcademicPhase, AcademicStatusType, EnrollmentStatus,
    GradeSubmissionStatus, OfficerRole, RegistrationStatus, RiskStatus,
    SponsorshipType, UserRole,
)


def _user(*, role: UserRole, email_slug: str) -> User:
    return User(
        id=uuid.uuid4(),
        email=f"{email_slug}@aau.edu.et",
        first_name="Test", last_name="User",
        hashed_password="not-a-real-hash",
        role=role, is_active=True,
    )


@pytest_asyncio.fixture
async def officer_user(async_session) -> User:
    user = _user(role=UserRole.REGISTRAR_OFFICER, email_slug="officer-eq")
    async_session.add(user)
    await async_session.flush()
    officer = CourseManagementOfficer(
        user_id=user.id, staff_id="REG/9100/15",
        role=OfficerRole.REGISTRAR_OFFICER, authorization_level=4,
    )
    async_session.add(officer)
    await async_session.flush()
    return user


@pytest_asyncio.fixture
async def admin_user(async_session) -> User:
    user = _user(role=UserRole.ADMIN, email_slug="admin-eq")
    async_session.add(user)
    await async_session.flush()
    return user


@pytest_asyncio.fixture
async def student_user_only(async_session) -> User:
    """Plain student — should be rejected by the queue's auth gate."""
    user = _user(role=UserRole.STUDENT, email_slug="just-student-eq")
    async_session.add(user)
    await async_session.flush()
    return user


@pytest_asyncio.fixture
async def queue_scenario(async_session):
    """
    Build a tiny world with one pending row per source so the queue
    join returns all three.
    """
    term = AcademicTerm(
        term_name="2024/2025",
        start_date=date(2024, 9, 1), end_date=date(2025, 1, 31),
        is_open=False, phase=AcademicPhase.ONE,
    )
    async_session.add(term)
    await async_session.flush()

    cs101 = Course(
        code="CS101", title="Intro", credit_hours=3,
        semester=1, department="Software Engineering",
    )
    async_session.add(cs101)
    await async_session.flush()

    section = Section(
        term_id=term.id, department="Software Engineering",
        semester=1, section_code="A", capacity=10, enrolled_count=0,
    )
    async_session.add(section)
    await async_session.flush()

    student_user = _user(role=UserRole.STUDENT, email_slug="alice-eq")
    instr_user = _user(role=UserRole.INSTRUCTOR, email_slug="instr-eq")
    async_session.add_all([student_user, instr_user])
    await async_session.flush()

    alice = Student(
        user_id=student_user.id,
        student_id="UGR/0001/15", full_name="Alice Eq",
        current_semester=1,
        department="Software Engineering",
        sponsorship_type=SponsorshipType.GOVERNMENT,
        enrollment_status=EnrollmentStatus.ACTIVE,
    )
    instructor = Instructor(
        user_id=instr_user.id, instructor_id="STAFF/0001/15",
        department="Software Engineering",
    )
    async_session.add_all([alice, instructor])
    await async_session.flush()

    async_session.add(Registration(
        student_id=alice.id, term_id=term.id,
        status=RegistrationStatus.REGISTERED,
        sponsorship_type=SponsorshipType.GOVERNMENT,
        section_id=section.id,
        finalised_at=datetime.now(timezone.utc),
    ))
    await async_session.flush()

    # ── 1. Advisory exception (HIGH risk awaiting officer review) ──
    advisory_at = datetime.now(timezone.utc) - timedelta(days=3)
    advisory = AdvisoryRecommendation(
        student_id=alice.id, term_id=term.id,
        risk_status=RiskStatus.HIGH,
        risk_explanation="Heavy load with weak history.",
        proposed_courses=[], recommended_courses=[], gap_analysis={},
        requires_officer_review=True,
    )
    async_session.add(advisory)
    await async_session.flush()
    advisory.created_at = advisory_at

    # ── 2. Grading exception (FLAGGED batch) ──
    breakdown = AssessmentBreakdown(
        section_id=section.id, course_id=cs101.id,
        instructor_id=instructor.id, term_id=term.id,
        created_by_id=instr_user.id,
    )
    async_session.add(breakdown)
    await async_session.flush()
    async_session.add(AssessmentComponent(
        breakdown_id=breakdown.id, name="Final",
        weight=100.0, max_score=100.0, order_index=1,
    ))
    await async_session.flush()
    batch = GradeBatch(
        section_id=section.id, course_id=cs101.id,
        instructor_id=instructor.id, term_id=term.id,
        breakdown_id=breakdown.id,
        status=GradeSubmissionStatus.FLAGGED,
        submitted_at=datetime.now(timezone.utc) - timedelta(days=2),
    )
    async_session.add(batch)
    await async_session.flush()

    # ── 3. Standing exception (held for review, INCOMPLETE) ──
    standing_at = datetime.now(timezone.utc) - timedelta(days=1)
    standing = AcademicStanding(
        student_id=alice.id, term_id=term.id,
        department="Software Engineering",
        sgpa=None, cgpa=None,
        term_credit_hours=0, cumulative_credit_hours=0,
        f_count_term=0, f_credit_total_term=0,
        is_first_semester=True, is_first_year=True,
        consecutive_warning_count=0,
        proposed_status=AcademicStatusType.INCOMPLETE,
        requires_review=True,
        computed_at=standing_at,
        computed_by_agent_id="AGENT_ASA_TEST",
    )
    async_session.add(standing)
    await async_session.flush()

    return {
        "term": term, "alice": alice, "section": section,
        "advisory": advisory, "batch": batch, "standing": standing,
    }


# ── Auth gate ───────────────────────────────────────────────────


async def test_queue_rejects_plain_student(
    async_session, student_user_only, queue_scenario,
):
    svc = ExceptionQueueService(async_session)
    with pytest.raises(UnauthorizedActorError):
        await svc.list_pending(user_id=student_user_only.id)


async def test_queue_accepts_admin(
    async_session, admin_user, queue_scenario,
):
    svc = ExceptionQueueService(async_session)
    out = await svc.list_pending(user_id=admin_user.id)
    assert len(out) == 3


async def test_queue_rejects_officer_role_without_officer_row(
    async_session,
):
    user = _user(role=UserRole.REGISTRAR_OFFICER, email_slug="phantom-officer-eq")
    async_session.add(user)
    await async_session.flush()
    svc = ExceptionQueueService(async_session)
    with pytest.raises(UnauthorizedActorError):
        await svc.list_pending(user_id=user.id)


# ── Join over the three sources ─────────────────────────────────


async def test_queue_returns_one_row_per_pending_source(
    async_session, officer_user, queue_scenario,
):
    svc = ExceptionQueueService(async_session)
    rows = await svc.list_pending(user_id=officer_user.id)
    assert len(rows) == 3
    sources = {r.source for r in rows}
    assert sources == {
        ExceptionSource.ADVISORY,
        ExceptionSource.GRADING,
        ExceptionSource.STANDING,
    }


async def test_queue_sorted_newest_first(
    async_session, officer_user, queue_scenario,
):
    svc = ExceptionQueueService(async_session)
    rows = await svc.list_pending(user_id=officer_user.id)
    # The standing was created most recently (1 day ago), then the
    # grading batch (2 days), then the advisory (3 days).
    assert rows[0].source is ExceptionSource.STANDING
    assert rows[1].source is ExceptionSource.GRADING
    assert rows[2].source is ExceptionSource.ADVISORY


async def test_queue_deep_links_are_source_specific(
    async_session, officer_user, queue_scenario,
):
    svc = ExceptionQueueService(async_session)
    rows = await svc.list_pending(user_id=officer_user.id)
    by_source = {r.source: r for r in rows}
    assert by_source[ExceptionSource.ADVISORY].deep_link == (
        f"/courses/advisory/recommendations/"
        f"{queue_scenario['advisory'].id}"
    )
    assert by_source[ExceptionSource.GRADING].deep_link == (
        f"/courses/grading/officer/batches/{queue_scenario['batch'].id}"
    )
    assert by_source[ExceptionSource.STANDING].deep_link == (
        f"/courses/standing/{queue_scenario['standing'].id}"
    )


# ── Filters ────────────────────────────────────────────────────


async def test_queue_filter_by_source(
    async_session, officer_user, queue_scenario,
):
    svc = ExceptionQueueService(async_session)
    rows = await svc.list_pending(
        user_id=officer_user.id,
        sources={ExceptionSource.STANDING},
    )
    assert len(rows) == 1
    assert rows[0].source is ExceptionSource.STANDING


async def test_queue_filter_by_term(
    async_session, officer_user, queue_scenario,
):
    svc = ExceptionQueueService(async_session)
    rows = await svc.list_pending(
        user_id=officer_user.id,
        term_id=queue_scenario["term"].id,
    )
    assert len(rows) == 3

    rows = await svc.list_pending(
        user_id=officer_user.id,
        term_id=uuid.uuid4(),  # bogus term
    )
    # Grading batch is keyed to a term too; bogus term filters out everything.
    assert rows == []


async def test_queue_filter_by_student(
    async_session, officer_user, queue_scenario,
):
    """student_id filter narrows ADVISORY + STANDING; GRADING is excluded."""
    svc = ExceptionQueueService(async_session)
    rows = await svc.list_pending(
        user_id=officer_user.id,
        student_id=queue_scenario["alice"].id,
        sources={ExceptionSource.ADVISORY, ExceptionSource.STANDING},
    )
    assert len(rows) == 2
    assert all(r.student_id == queue_scenario["alice"].id for r in rows)


# ── Excluded states ────────────────────────────────────────────


async def test_already_reviewed_advisory_is_excluded(
    async_session, officer_user, queue_scenario,
):
    """Setting reviewed_at on the advisory drops it from the queue."""
    advisory = queue_scenario["advisory"]
    advisory.reviewed_at = datetime.now(timezone.utc)
    await async_session.flush()

    svc = ExceptionQueueService(async_session)
    rows = await svc.list_pending(
        user_id=officer_user.id,
        sources={ExceptionSource.ADVISORY},
    )
    assert rows == []


async def test_authorised_standing_is_excluded(
    async_session, officer_user, queue_scenario,
):
    """A standing with final_status set drops out of the queue."""
    standing = queue_scenario["standing"]
    standing.final_status = AcademicStatusType.INCOMPLETE
    standing.authorised_at = datetime.now(timezone.utc)
    await async_session.flush()

    svc = ExceptionQueueService(async_session)
    rows = await svc.list_pending(
        user_id=officer_user.id,
        sources={ExceptionSource.STANDING},
    )
    assert rows == []


async def test_authorised_grade_batch_is_excluded(
    async_session, officer_user, queue_scenario,
):
    """A batch transitioning to AUTHORISED drops out of the queue."""
    batch = queue_scenario["batch"]
    batch.status = GradeSubmissionStatus.AUTHORISED
    await async_session.flush()

    svc = ExceptionQueueService(async_session)
    rows = await svc.list_pending(
        user_id=officer_user.id,
        sources={ExceptionSource.GRADING},
    )
    assert rows == []


# ── Summary content ────────────────────────────────────────────


async def test_advisory_entry_has_high_risk_summary(
    async_session, officer_user, queue_scenario,
):
    svc = ExceptionQueueService(async_session)
    rows = await svc.list_pending(
        user_id=officer_user.id,
        sources={ExceptionSource.ADVISORY},
    )
    assert "HIGH" in rows[0].summary


async def test_standing_entry_has_review_summary(
    async_session, officer_user, queue_scenario,
):
    svc = ExceptionQueueService(async_session)
    rows = await svc.list_pending(
        user_id=officer_user.id,
        sources={ExceptionSource.STANDING},
    )
    assert "held for review" in rows[0].summary.lower()


async def test_grading_entry_carries_course_and_section_info(
    async_session, officer_user, queue_scenario,
):
    svc = ExceptionQueueService(async_session)
    rows = await svc.list_pending(
        user_id=officer_user.id,
        sources={ExceptionSource.GRADING},
    )
    assert "CS101" in rows[0].summary
    assert rows[0].department == "Software Engineering"
