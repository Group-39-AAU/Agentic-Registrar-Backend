"""
Track C PR C2 — Standing service compute / authorise / override tests.

End-to-end coverage of the write paths:
  * compute_term_standing — upserts AcademicStanding rows and
    fans the rule trail through to the response.
  * authorise              — final_status set; history row written.
  * override               — final_status overridden with reason.
  * Auth gate              — DH-only for writes.
  * list_queue             — pending vs all standings.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.modules.auth.models import User
from app.modules.course.exceptions import (
    InvalidAdjustmentRequestError, InvalidStateTransitionError,
    UnauthorizedActorError,
)
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


# ── Fixtures ────────────────────────────────────────────────────


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
    user = _user(role=UserRole.REGISTRAR_OFFICER, email_slug="dh-c2")
    async_session.add(user)
    await async_session.flush()
    officer = CourseManagementOfficer(
        user_id=user.id, staff_id="REG/0900/15",
        role=OfficerRole.DEPARTMENT_HEAD,
        authorization_level=5,
    )
    async_session.add(officer)
    await async_session.flush()
    return user


@pytest_asyncio.fixture
async def registrar_user(async_session) -> User:
    """Plain registrar officer — can read, cannot write."""
    user = _user(role=UserRole.REGISTRAR_OFFICER, email_slug="reg-c2")
    async_session.add(user)
    await async_session.flush()
    officer = CourseManagementOfficer(
        user_id=user.id, staff_id="REG/0901/15",
        role=OfficerRole.REGISTRAR_OFFICER,
        authorization_level=3,
    )
    async_session.add(officer)
    await async_session.flush()
    return user


@pytest_asyncio.fixture
async def compute_scenario(async_session):
    """
    Three students with distinct grade profiles in one (term, section):
      Alice   — A+ A+ A      → SGPA 4.00, will PROMOTE
      Bob     — C C C        → SGPA 2.00, will PROMOTE (good standing)
      Carol   — F F D        → SGPA 0.40, will DISMISS (91.7.3)
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

    cs101 = Course(
        code="CS101", title="Intro", credit_hours=3,
        semester=1, department="Software Engineering",
    )
    cs102 = Course(
        code="CS102", title="Discrete", credit_hours=3,
        semester=1, department="Software Engineering",
    )
    math101 = Course(
        code="MATH101", title="Calculus I", credit_hours=4,
        semester=1, department="Software Engineering",
    )
    async_session.add_all([cs101, cs102, math101])
    await async_session.flush()

    section = Section(
        term_id=term.id, department="Software Engineering",
        semester=1, section_code="A", capacity=10, enrolled_count=0,
    )
    async_session.add(section)
    await async_session.flush()

    students: list[Student] = []
    for i, name in enumerate(("Alice", "Bob", "Carol"), start=1):
        u = _user(role=UserRole.STUDENT, email_slug=f"stu-c2-{i:02d}")
        async_session.add(u)
        await async_session.flush()
        s = Student(
            user_id=u.id,
            student_id=f"UGR/00{i:02d}/15",
            full_name=name,
            current_semester=1,
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

    grade_map = {
        students[0]: [
            (cs101, GradeLetter.A_PLUS), (cs102, GradeLetter.A_PLUS),
            (math101, GradeLetter.A),
        ],
        students[1]: [
            (cs101, GradeLetter.C), (cs102, GradeLetter.C),
            (math101, GradeLetter.C),
        ],
        students[2]: [
            (cs101, GradeLetter.F), (cs102, GradeLetter.F),
            (math101, GradeLetter.D),
        ],
    }
    for s, rows in grade_map.items():
        for course, letter in rows:
            pts = points_for(letter)
            async_session.add(Grade(
                student_id=s.id, course_id=course.id, term_id=term.id,
                section_id=section.id,
                letter_grade=letter, numeric_score=None,
                credit_hours=course.credit_hours,
                grade_points=(pts or 0.0) * course.credit_hours,
                status=GradeSubmissionStatus.AUTHORISED,
                entered_at=datetime.now(timezone.utc),
                authorised_at=datetime.now(timezone.utc),
            ))
    await async_session.flush()

    return {
        "term": term, "section": section, "students": students,
        "cs101": cs101, "cs102": cs102, "math101": math101,
    }


# ── Compute ─────────────────────────────────────────────────────


async def test_compute_creates_one_standing_per_student(
    async_session, dh_user, compute_scenario,
):
    svc = StandingService(async_session)
    rows, computed, skipped, _ = await svc.compute_term_standing(
        user_id=dh_user.id, term_id=compute_scenario["term"].id,
    )
    assert computed == 3
    assert skipped == 0
    assert len(rows) == 3

    persisted = (await async_session.execute(select(AcademicStanding))).scalars().all()
    assert len(persisted) == 3


async def test_compute_emits_proposed_status_per_rules(
    async_session, dh_user, compute_scenario,
):
    svc = StandingService(async_session)
    rows, _, _, _ = await svc.compute_term_standing(
        user_id=dh_user.id, term_id=compute_scenario["term"].id,
    )
    by_name = {
        r.standing.student_id: r for r in rows
    }
    alice = by_name[compute_scenario["students"][0].id]
    bob = by_name[compute_scenario["students"][1].id]
    carol = by_name[compute_scenario["students"][2].id]

    assert alice.standing.proposed_status is AcademicStatusType.PROMOTED
    assert bob.standing.proposed_status is AcademicStatusType.PROMOTED
    # Carol: SGPA=4/10=0.40, CGPA=0.40 → 91.7.3 dismissal.
    assert carol.standing.proposed_status is AcademicStatusType.DISMISSED
    assert any("91.7.3" in c for c in carol.rule_citations)


async def test_compute_writes_proposed_history(
    async_session, dh_user, compute_scenario,
):
    svc = StandingService(async_session)
    await svc.compute_term_standing(
        user_id=dh_user.id, term_id=compute_scenario["term"].id,
    )
    histories = (await async_session.execute(
        select(AcademicStandingHistory)
    )).scalars().all()
    assert len(histories) == 3
    assert all(h.event == "PROPOSED" for h in histories)


async def test_compute_is_idempotent_re_runs_record_recomputed(
    async_session, dh_user, compute_scenario,
):
    svc = StandingService(async_session)
    await svc.compute_term_standing(
        user_id=dh_user.id, term_id=compute_scenario["term"].id,
    )
    await svc.compute_term_standing(
        user_id=dh_user.id, term_id=compute_scenario["term"].id,
    )

    standings = (await async_session.execute(select(AcademicStanding))).scalars().all()
    assert len(standings) == 3  # no duplicates

    histories = (await async_session.execute(select(AcademicStandingHistory))).scalars().all()
    # 3 PROPOSED + 3 RE_COMPUTED.
    assert len(histories) == 6
    events = sorted(h.event for h in histories)
    assert events == ["PROPOSED"] * 3 + ["RE_COMPUTED"] * 3


async def test_compute_scope_filter_by_section(
    async_session, dh_user, compute_scenario,
):
    """An out-of-scope section_id yields zero computed rows."""
    svc = StandingService(async_session)
    rows, computed, _, _ = await svc.compute_term_standing(
        user_id=dh_user.id, term_id=compute_scenario["term"].id,
        section_id=uuid.uuid4(),
    )
    assert computed == 0
    assert rows == []


async def test_compute_skips_already_authorised(
    async_session, dh_user, compute_scenario,
):
    """After authorise, re-compute does NOT change final_status."""
    svc = StandingService(async_session)
    await svc.compute_term_standing(
        user_id=dh_user.id, term_id=compute_scenario["term"].id,
    )

    alice_standing = (await async_session.execute(
        select(AcademicStanding).where(
            AcademicStanding.student_id == compute_scenario["students"][0].id,
        )
    )).scalar_one()
    await svc.authorise(
        user_id=dh_user.id, standing_id=alice_standing.id,
    )

    rows, computed, skipped, _ = await svc.compute_term_standing(
        user_id=dh_user.id, term_id=compute_scenario["term"].id,
    )
    # 2 students still pending, 1 already authorised → skipped.
    assert computed == 2
    assert skipped == 1


# ── Auth gate ───────────────────────────────────────────────────


async def test_compute_rejects_non_dh_officer(
    async_session, registrar_user, compute_scenario,
):
    svc = StandingService(async_session)
    with pytest.raises(UnauthorizedActorError):
        await svc.compute_term_standing(
            user_id=registrar_user.id,
            term_id=compute_scenario["term"].id,
        )


async def test_authorise_rejects_non_dh_officer(
    async_session, dh_user, registrar_user, compute_scenario,
):
    svc = StandingService(async_session)
    rows, _, _, _ = await svc.compute_term_standing(
        user_id=dh_user.id, term_id=compute_scenario["term"].id,
    )
    standing_id = rows[0].standing.id
    with pytest.raises(UnauthorizedActorError):
        await svc.authorise(
            user_id=registrar_user.id, standing_id=standing_id,
        )


async def test_admin_can_authorise(async_session, compute_scenario):
    admin = _user(role=UserRole.ADMIN, email_slug="admin-c2")
    async_session.add(admin)
    await async_session.flush()

    svc = StandingService(async_session)
    rows, _, _, _ = await svc.compute_term_standing(
        user_id=admin.id, term_id=compute_scenario["term"].id,
    )
    standing = await svc.authorise(
        user_id=admin.id, standing_id=rows[0].standing.id,
    )
    assert standing.final_status is not None


# ── Authorise ───────────────────────────────────────────────────


async def test_authorise_sets_final_status_to_proposed(
    async_session, dh_user, compute_scenario,
):
    svc = StandingService(async_session)
    rows, _, _, _ = await svc.compute_term_standing(
        user_id=dh_user.id, term_id=compute_scenario["term"].id,
    )
    standing_id = rows[0].standing.id
    standing = await svc.authorise(
        user_id=dh_user.id, standing_id=standing_id,
    )
    assert standing.final_status is standing.proposed_status
    assert standing.authorised_by_id == dh_user.id
    assert standing.authorised_at is not None


async def test_authorise_writes_history(
    async_session, dh_user, compute_scenario,
):
    svc = StandingService(async_session)
    rows, _, _, _ = await svc.compute_term_standing(
        user_id=dh_user.id, term_id=compute_scenario["term"].id,
    )
    standing_id = rows[0].standing.id
    await svc.authorise(user_id=dh_user.id, standing_id=standing_id)

    history = (await async_session.execute(
        select(AcademicStandingHistory).where(
            AcademicStandingHistory.standing_id == standing_id,
            AcademicStandingHistory.event == "AUTHORISED",
        )
    )).scalar_one()
    assert history.changed_by_id == dh_user.id


async def test_authorise_is_idempotent(
    async_session, dh_user, compute_scenario,
):
    svc = StandingService(async_session)
    rows, _, _, _ = await svc.compute_term_standing(
        user_id=dh_user.id, term_id=compute_scenario["term"].id,
    )
    standing_id = rows[0].standing.id
    first = await svc.authorise(user_id=dh_user.id, standing_id=standing_id)
    second = await svc.authorise(user_id=dh_user.id, standing_id=standing_id)

    assert first.id == second.id
    assert first.authorised_at == second.authorised_at

    # Only ONE AUTHORISED history row.
    histories = (await async_session.execute(
        select(AcademicStandingHistory).where(
            AcademicStandingHistory.standing_id == standing_id,
            AcademicStandingHistory.event == "AUTHORISED",
        )
    )).scalars().all()
    assert len(histories) == 1


async def test_authorise_held_for_review_requires_reason(
    async_session, dh_user, compute_scenario,
):
    """Standing with requires_review=True needs a written reason."""
    # Inject an I-grade for Bob so his standing is INCOMPLETE.
    bob = compute_scenario["students"][1]
    cs101 = compute_scenario["cs101"]
    from sqlalchemy import update
    await async_session.execute(
        update(Grade)
        .where(
            Grade.student_id == bob.id,
            Grade.course_id == cs101.id,
        )
        .values(letter_grade=GradeLetter.I, grade_points=None)
    )
    await async_session.flush()

    svc = StandingService(async_session)
    rows, _, _, _ = await svc.compute_term_standing(
        user_id=dh_user.id, term_id=compute_scenario["term"].id,
    )
    bob_row = next(
        r for r in rows
        if r.standing.student_id == bob.id
    )
    assert bob_row.standing.requires_review is True

    with pytest.raises(InvalidStateTransitionError):
        await svc.authorise(
            user_id=dh_user.id, standing_id=bob_row.standing.id,
        )

    # Works with a written reason.
    out = await svc.authorise(
        user_id=dh_user.id, standing_id=bob_row.standing.id,
        reason="Coursework completed retroactively per AC ruling.",
    )
    assert out.final_status is not None


# ── Override ────────────────────────────────────────────────────


async def test_override_sets_new_status(
    async_session, dh_user, compute_scenario,
):
    svc = StandingService(async_session)
    rows, _, _, _ = await svc.compute_term_standing(
        user_id=dh_user.id, term_id=compute_scenario["term"].id,
    )
    # Carol was DISMISSED — override to WARNING with a written reason.
    carol_id = next(
        r.standing.id for r in rows
        if r.standing.proposed_status is AcademicStatusType.DISMISSED
    )
    out = await svc.override(
        user_id=dh_user.id, standing_id=carol_id,
        new_status=AcademicStatusType.WARNING,
        reason="Documented medical emergency mid-term; one-term reprieve granted.",
    )
    assert out.final_status is AcademicStatusType.WARNING
    assert out.proposed_status is AcademicStatusType.DISMISSED
    assert out.override_reason is not None


async def test_override_rejects_missing_reason(
    async_session, dh_user, compute_scenario,
):
    svc = StandingService(async_session)
    rows, _, _, _ = await svc.compute_term_standing(
        user_id=dh_user.id, term_id=compute_scenario["term"].id,
    )
    standing_id = rows[0].standing.id
    with pytest.raises(InvalidAdjustmentRequestError):
        await svc.override(
            user_id=dh_user.id, standing_id=standing_id,
            new_status=AcademicStatusType.WARNING,
            reason="   ",
        )


async def test_override_writes_history(
    async_session, dh_user, compute_scenario,
):
    svc = StandingService(async_session)
    rows, _, _, _ = await svc.compute_term_standing(
        user_id=dh_user.id, term_id=compute_scenario["term"].id,
    )
    standing_id = rows[0].standing.id
    await svc.override(
        user_id=dh_user.id, standing_id=standing_id,
        new_status=AcademicStatusType.WARNING,
        reason="A long enough justification to clear the schema-level guards.",
    )

    history = (await async_session.execute(
        select(AcademicStandingHistory).where(
            AcademicStandingHistory.standing_id == standing_id,
            AcademicStandingHistory.event == "OVERRIDDEN",
        )
    )).scalar_one()
    assert history.changed_by_id == dh_user.id
    assert history.new_status is AcademicStatusType.WARNING


# ── Queue ───────────────────────────────────────────────────────


async def test_list_queue_default_pending_only(
    async_session, dh_user, compute_scenario,
):
    svc = StandingService(async_session)
    await svc.compute_term_standing(
        user_id=dh_user.id, term_id=compute_scenario["term"].id,
    )
    pending = await svc.list_queue(
        user_id=dh_user.id, term_id=compute_scenario["term"].id,
    )
    assert len(pending) == 3


async def test_list_queue_excludes_authorised(
    async_session, dh_user, compute_scenario,
):
    svc = StandingService(async_session)
    rows, _, _, _ = await svc.compute_term_standing(
        user_id=dh_user.id, term_id=compute_scenario["term"].id,
    )
    await svc.authorise(
        user_id=dh_user.id, standing_id=rows[0].standing.id,
    )
    pending = await svc.list_queue(
        user_id=dh_user.id, term_id=compute_scenario["term"].id,
    )
    assert len(pending) == 2  # one was authorised


async def test_list_queue_includes_authorised_when_only_pending_false(
    async_session, dh_user, compute_scenario,
):
    svc = StandingService(async_session)
    rows, _, _, _ = await svc.compute_term_standing(
        user_id=dh_user.id, term_id=compute_scenario["term"].id,
    )
    await svc.authorise(
        user_id=dh_user.id, standing_id=rows[0].standing.id,
    )
    everyone = await svc.list_queue(
        user_id=dh_user.id, term_id=compute_scenario["term"].id,
        only_pending=False,
    )
    assert len(everyone) == 3


# ── Get + 404 ───────────────────────────────────────────────────


async def test_get_standing_returns_row(
    async_session, dh_user, compute_scenario,
):
    svc = StandingService(async_session)
    rows, _, _, _ = await svc.compute_term_standing(
        user_id=dh_user.id, term_id=compute_scenario["term"].id,
    )
    standing = await svc.get_standing(
        user_id=dh_user.id, standing_id=rows[0].standing.id,
    )
    assert standing.id == rows[0].standing.id
