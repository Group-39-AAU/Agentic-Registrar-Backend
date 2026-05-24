"""
Track C — Batch authorise + list-all-standings tests.

Covers ``StandingService.batch_authorise`` and the broader
``StandingService.list_standings`` filters added on top of the
PR C2 single-row authorise.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select, update

from app.modules.auth.models import User
from app.modules.course.exceptions import UnauthorizedActorError
from app.modules.course.grade_points import points_for
from app.modules.course.models import (
    AcademicTerm, Course, CourseManagementOfficer, Grade, Registration,
    Section, Student,
)
from app.modules.course.standing.models import (
    AcademicStanding, AcademicStandingHistory,
)
from app.modules.course.standing.service import StandingService
from app.shared.enums import (
    AcademicPhase, AcademicStatusType, EnrollmentStatus, GradeLetter,
    GradeSubmissionStatus, OfficerRole, RegistrationStatus, SponsorshipType,
    UserRole,
)


def _user(*, role: UserRole, email_slug: str) -> User:
    return User(
        id=uuid.uuid4(),
        email=f"{email_slug}@aau.edu.et",
        first_name="Test",
        last_name="User",
        hashed_password="not-a-real-hash",
        role=role,
        is_active=True,
    )


@pytest_asyncio.fixture
async def dh_user(async_session) -> User:
    user = _user(role=UserRole.REGISTRAR_OFFICER, email_slug="dh-batch")
    async_session.add(user)
    await async_session.flush()
    officer = CourseManagementOfficer(
        user_id=user.id, staff_id="REG/0950/15",
        role=OfficerRole.DEPARTMENT_HEAD, authorization_level=5,
    )
    async_session.add(officer)
    await async_session.flush()
    return user


@pytest_asyncio.fixture
async def registrar_user(async_session) -> User:
    user = _user(role=UserRole.REGISTRAR_OFFICER, email_slug="reg-batch")
    async_session.add(user)
    await async_session.flush()
    officer = CourseManagementOfficer(
        user_id=user.id, staff_id="REG/0951/15",
        role=OfficerRole.REGISTRAR_OFFICER, authorization_level=3,
    )
    async_session.add(officer)
    await async_session.flush()
    return user


@pytest_asyncio.fixture
async def computed_scenario(async_session, dh_user):
    """
    A computed scenario with 4 students and one I-grade student so
    we exercise: pending, held-for-review, and (after explicit call)
    already-authorised.

      Alice — A+ A+ A      → PROMOTED  (clean proposal)
      Bob   — B B B        → PROMOTED  (clean proposal)
      Carol — F F F        → DISMISSED (clean proposal)
      Dave  — I A A        → INCOMPLETE (held for review)
    """
    term = AcademicTerm(
        term_name="2024/2025",
        start_date=date(2024, 9, 1),
        end_date=date(2025, 1, 31),
        is_open=False,
        phase=AcademicPhase.ONE,
    )
    async_session.add(term)
    await async_session.flush()

    cs1 = Course(
        code="CS101", title="C", credit_hours=3,
        semester=1, department="Software Engineering",
    )
    cs2 = Course(
        code="CS102", title="D", credit_hours=3,
        semester=1, department="Software Engineering",
    )
    m1 = Course(
        code="MATH101", title="M", credit_hours=3,
        semester=1, department="Software Engineering",
    )
    async_session.add_all([cs1, cs2, m1])
    await async_session.flush()

    section = Section(
        term_id=term.id, department="Software Engineering",
        semester=1, section_code="A", capacity=10, enrolled_count=0,
    )
    async_session.add(section)
    await async_session.flush()

    students: list[Student] = []
    for i, name in enumerate(("Alice", "Bob", "Carol", "Dave"), start=1):
        u = _user(role=UserRole.STUDENT, email_slug=f"stu-batch-{i:02d}")
        async_session.add(u)
        await async_session.flush()
        s = Student(
            user_id=u.id, student_id=f"UGR/00{i:02d}/15",
            full_name=name, current_semester=1,
            department="Software Engineering",
            sponsorship_type=SponsorshipType.GOVERNMENT,
            enrollment_status=EnrollmentStatus.ACTIVE,
        )
        async_session.add(s)
        students.append(s)
    await async_session.flush()

    for s in students:
        async_session.add(Registration(
            student_id=s.id, term_id=term.id,
            status=RegistrationStatus.REGISTERED,
            sponsorship_type=SponsorshipType.GOVERNMENT,
            section_id=section.id,
            finalised_at=datetime.now(timezone.utc),
        ))
    section.enrolled_count = len(students)
    await async_session.flush()

    grades = {
        students[0]: [(cs1, GradeLetter.A_PLUS), (cs2, GradeLetter.A_PLUS), (m1, GradeLetter.A)],
        students[1]: [(cs1, GradeLetter.B), (cs2, GradeLetter.B), (m1, GradeLetter.B)],
        students[2]: [(cs1, GradeLetter.F), (cs2, GradeLetter.F), (m1, GradeLetter.F)],
        students[3]: [(cs1, GradeLetter.I), (cs2, GradeLetter.A), (m1, GradeLetter.A)],
    }
    for s, rows in grades.items():
        for course, letter in rows:
            pts = points_for(letter)
            async_session.add(Grade(
                student_id=s.id, course_id=course.id, term_id=term.id,
                section_id=section.id,
                letter_grade=letter,
                credit_hours=course.credit_hours,
                grade_points=(pts * course.credit_hours) if pts is not None else None,
                status=GradeSubmissionStatus.AUTHORISED,
                entered_at=datetime.now(timezone.utc),
                authorised_at=datetime.now(timezone.utc),
            ))
    await async_session.flush()

    # Run compute so AcademicStanding rows exist.
    svc = StandingService(async_session)
    await svc.compute_term_standing(user_id=dh_user.id, term_id=term.id)

    standings = (await async_session.execute(
        select(AcademicStanding).order_by(Student.student_id.asc())
        .join(Student, Student.id == AcademicStanding.student_id)
    )).scalars().all()

    by_name = {s.full_name: standings_row for s, standings_row in zip(
        students,
        sorted(
            standings,
            key=lambda r: next(
                stu.student_id for stu in students if stu.id == r.student_id
            ),
        ),
    )}
    return {
        "term": term, "students": students,
        "standings_by_name": by_name,
    }


# ── Batch authorise: happy path ────────────────────────────────


async def test_batch_authorises_clean_proposals(
    async_session, dh_user, computed_scenario,
):
    """Three clean proposals authorised in one call."""
    svc = StandingService(async_session)
    standings = computed_scenario["standings_by_name"]
    payload_ids = [
        standings["Alice"].id, standings["Bob"].id, standings["Carol"].id,
    ]
    result = await svc.batch_authorise(
        user_id=dh_user.id, standing_ids=payload_ids,
    )
    assert result.requested_count == 3
    assert result.authorised_count == 3
    assert result.already_authorised_count == 0
    assert result.held_needs_reason_count == 0
    assert result.not_found_count == 0
    assert all(r.status == "AUTHORISED" for r in result.rows)

    # All three rows now have final_status set.
    persisted = (await async_session.execute(
        select(AcademicStanding).where(
            AcademicStanding.id.in_(payload_ids),
        )
    )).scalars().all()
    assert all(p.final_status is not None for p in persisted)
    assert all(p.authorised_by_id == dh_user.id for p in persisted)


async def test_batch_writes_one_history_row_per_authorisation(
    async_session, dh_user, computed_scenario,
):
    svc = StandingService(async_session)
    standings = computed_scenario["standings_by_name"]
    ids = [standings["Alice"].id, standings["Bob"].id]
    await svc.batch_authorise(user_id=dh_user.id, standing_ids=ids)

    histories = (await async_session.execute(
        select(AcademicStandingHistory).where(
            AcademicStandingHistory.standing_id.in_(ids),
            AcademicStandingHistory.event == "AUTHORISED",
        )
    )).scalars().all()
    assert len(histories) == 2
    assert all(h.changed_by_id == dh_user.id for h in histories)


# ── Batch authorise: per-row outcomes ──────────────────────────


async def test_batch_skips_held_for_review_without_reason(
    async_session, dh_user, computed_scenario,
):
    """Dave's standing is INCOMPLETE → skipped without a reason."""
    svc = StandingService(async_session)
    standings = computed_scenario["standings_by_name"]
    result = await svc.batch_authorise(
        user_id=dh_user.id,
        standing_ids=[standings["Alice"].id, standings["Dave"].id],
    )
    assert result.authorised_count == 1
    assert result.held_needs_reason_count == 1
    by_id = {r.standing_id: r for r in result.rows}
    assert by_id[standings["Alice"].id].status == "AUTHORISED"
    assert by_id[standings["Dave"].id].status == "HELD_NEEDS_REASON"

    # Dave is still pending.
    dave = (await async_session.execute(
        select(AcademicStanding).where(
            AcademicStanding.id == standings["Dave"].id,
        )
    )).scalar_one()
    assert dave.final_status is None


async def test_batch_authorises_held_for_review_with_reason(
    async_session, dh_user, computed_scenario,
):
    svc = StandingService(async_session)
    standings = computed_scenario["standings_by_name"]
    result = await svc.batch_authorise(
        user_id=dh_user.id,
        standing_ids=[standings["Alice"].id, standings["Dave"].id],
        reason="AC reviewed pending coursework and granted clearance.",
    )
    assert result.authorised_count == 2
    assert result.held_needs_reason_count == 0
    by_id = {r.standing_id: r for r in result.rows}
    assert by_id[standings["Dave"].id].status == "AUTHORISED"

    dave = (await async_session.execute(
        select(AcademicStanding).where(
            AcademicStanding.id == standings["Dave"].id,
        )
    )).scalar_one()
    assert dave.final_status is not None
    assert dave.override_reason is not None


async def test_batch_marks_already_authorised_rows(
    async_session, dh_user, computed_scenario,
):
    svc = StandingService(async_session)
    standings = computed_scenario["standings_by_name"]
    alice_id = standings["Alice"].id
    # Authorise Alice individually first.
    await svc.authorise(user_id=dh_user.id, standing_id=alice_id)

    result = await svc.batch_authorise(
        user_id=dh_user.id,
        standing_ids=[alice_id, standings["Bob"].id],
    )
    assert result.authorised_count == 1
    assert result.already_authorised_count == 1
    by_id = {r.standing_id: r for r in result.rows}
    assert by_id[alice_id].status == "ALREADY_AUTHORISED"
    assert by_id[standings["Bob"].id].status == "AUTHORISED"


async def test_batch_reports_not_found_for_unknown_ids(
    async_session, dh_user, computed_scenario,
):
    svc = StandingService(async_session)
    standings = computed_scenario["standings_by_name"]
    bogus = uuid.uuid4()
    result = await svc.batch_authorise(
        user_id=dh_user.id,
        standing_ids=[standings["Alice"].id, bogus],
    )
    assert result.authorised_count == 1
    assert result.not_found_count == 1
    by_id = {r.standing_id: r for r in result.rows}
    assert by_id[bogus].status == "NOT_FOUND"


async def test_batch_deduplicates_ids(
    async_session, dh_user, computed_scenario,
):
    """Repeating the same id shouldn't authorise twice or fail."""
    svc = StandingService(async_session)
    standings = computed_scenario["standings_by_name"]
    aid = standings["Alice"].id
    result = await svc.batch_authorise(
        user_id=dh_user.id, standing_ids=[aid, aid, aid],
    )
    assert result.requested_count == 1  # dedup happens upstream
    assert result.authorised_count == 1


# ── Batch authorise: auth gate ──────────────────────────────────


async def test_batch_rejects_non_dh_officer(
    async_session, registrar_user, computed_scenario,
):
    svc = StandingService(async_session)
    standings = computed_scenario["standings_by_name"]
    with pytest.raises(UnauthorizedActorError):
        await svc.batch_authorise(
            user_id=registrar_user.id,
            standing_ids=[standings["Alice"].id],
        )


# ── list_standings: broader filters ────────────────────────────


async def test_list_all_default_returns_every_row(
    async_session, dh_user, computed_scenario,
):
    svc = StandingService(async_session)
    rows = await svc.list_standings(user_id=dh_user.id)
    # 4 students each got a standing row.
    assert len(rows) == 4


async def test_list_all_filter_by_proposed_status(
    async_session, dh_user, computed_scenario,
):
    svc = StandingService(async_session)
    promoted = await svc.list_standings(
        user_id=dh_user.id,
        proposed_status=AcademicStatusType.PROMOTED,
    )
    # Alice + Bob are PROMOTED.
    assert len(promoted) == 2

    dismissed = await svc.list_standings(
        user_id=dh_user.id,
        proposed_status=AcademicStatusType.DISMISSED,
    )
    assert len(dismissed) == 1


async def test_list_all_filter_by_requires_review(
    async_session, dh_user, computed_scenario,
):
    svc = StandingService(async_session)
    held = await svc.list_standings(
        user_id=dh_user.id, requires_review=True,
    )
    # Only Dave's incomplete row.
    assert len(held) == 1


async def test_list_all_filter_by_student(
    async_session, dh_user, computed_scenario,
):
    svc = StandingService(async_session)
    alice = computed_scenario["students"][0]
    rows = await svc.list_standings(
        user_id=dh_user.id, student_id=alice.id,
    )
    assert len(rows) == 1
    assert rows[0].student_id == alice.id


async def test_list_all_filter_by_final_status_after_authorise(
    async_session, dh_user, computed_scenario,
):
    svc = StandingService(async_session)
    standings = computed_scenario["standings_by_name"]
    await svc.authorise(user_id=dh_user.id, standing_id=standings["Alice"].id)

    promoted_final = await svc.list_standings(
        user_id=dh_user.id,
        final_status=AcademicStatusType.PROMOTED,
    )
    assert len(promoted_final) == 1
    assert promoted_final[0].id == standings["Alice"].id


async def test_list_all_only_pending_excludes_authorised(
    async_session, dh_user, computed_scenario,
):
    svc = StandingService(async_session)
    standings = computed_scenario["standings_by_name"]
    await svc.authorise(user_id=dh_user.id, standing_id=standings["Alice"].id)

    pending = await svc.list_standings(
        user_id=dh_user.id, only_pending=True,
    )
    # Alice now authorised → 3 remaining pending.
    assert len(pending) == 3


async def test_list_queue_backward_compat_delegates(
    async_session, dh_user, computed_scenario,
):
    """``list_queue`` keeps the old workflow-queue defaults."""
    svc = StandingService(async_session)
    standings = computed_scenario["standings_by_name"]
    await svc.authorise(user_id=dh_user.id, standing_id=standings["Bob"].id)

    queue = await svc.list_queue(user_id=dh_user.id)
    # only_pending defaults to True for the queue.
    assert len(queue) == 3
