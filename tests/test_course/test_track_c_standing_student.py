"""
Track C PR C3 — Student-facing standing endpoints + transcript bolt-on.

Covers:
  * StandingService.get_my_standings — multi-term view, newest-first,
    AUTHORISED-only filtering, current_status derivation.
  * StandingService.get_my_term_standing — single-term view, 404 when
    not authorised.
  * Transcript extension — TranscriptTermEntry.academic_status surfaces
    when the standing is authorised, otherwise stays null.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.modules.auth.models import User
from app.modules.course.exceptions import (
    EntityNotFoundError, StudentProfileRequiredError,
)
from app.modules.course.grade_points import points_for
from app.modules.course.grading.transcript_service import (
    StudentTranscriptService,
)
from app.modules.course.models import (
    AcademicTerm, Course, CourseManagementOfficer, Grade, Registration,
    Section, Student,
)
from app.modules.course.standing.models import AcademicStanding
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
    user = _user(role=UserRole.REGISTRAR_OFFICER, email_slug="dh-c3")
    async_session.add(user)
    await async_session.flush()
    officer = CourseManagementOfficer(
        user_id=user.id, staff_id="REG/0980/15",
        role=OfficerRole.DEPARTMENT_HEAD, authorization_level=5,
    )
    async_session.add(officer)
    await async_session.flush()
    return user


@pytest_asyncio.fixture
async def student_scenario(async_session, dh_user):
    """
    One student with two-term history:
      Term 1 (closed):  Authorised standing PROMOTED (clean Bs/As)
      Term 2 (closed):  COMPUTED but NOT yet authorised (DISMISSED proposal)

    Plus a second student in Term 1 who never had a standing computed
    so we exercise the "404 / not found" path.
    """
    # ── Terms ──
    term1 = AcademicTerm(
        term_name="2023/2024",
        start_date=date(2023, 9, 1),
        end_date=date(2024, 1, 31),
        is_open=False, phase=AcademicPhase.ONE,
    )
    term2 = AcademicTerm(
        term_name="2024/2025",
        start_date=date(2024, 9, 1),
        end_date=date(2025, 1, 31),
        is_open=False, phase=AcademicPhase.ONE,
    )
    async_session.add_all([term1, term2])
    await async_session.flush()

    # ── Courses ──
    cs101 = Course(
        code="CS101", title="Intro", credit_hours=3,
        semester=1, department="Software Engineering",
    )
    cs102 = Course(
        code="CS102", title="Discrete", credit_hours=3,
        semester=1, department="Software Engineering",
    )
    math101 = Course(
        code="MATH101", title="Calc I", credit_hours=4,
        semester=1, department="Software Engineering",
    )
    async_session.add_all([cs101, cs102, math101])
    await async_session.flush()

    # ── Sections ──
    s1 = Section(
        term_id=term1.id, department="Software Engineering",
        semester=1, section_code="A", capacity=10, enrolled_count=0,
    )
    s2 = Section(
        term_id=term2.id, department="Software Engineering",
        semester=1, section_code="A", capacity=10, enrolled_count=0,
    )
    async_session.add_all([s1, s2])
    await async_session.flush()

    # ── Students ──
    student_user_alice = _user(role=UserRole.STUDENT, email_slug="alice-c3")
    student_user_bob = _user(role=UserRole.STUDENT, email_slug="bob-c3")
    async_session.add_all([student_user_alice, student_user_bob])
    await async_session.flush()

    alice = Student(
        user_id=student_user_alice.id,
        student_id="UGR/0001/15",
        full_name="Alice Demoo",
        current_semester=2,
        department="Software Engineering",
        sponsorship_type=SponsorshipType.GOVERNMENT,
        enrollment_status=EnrollmentStatus.ACTIVE,
    )
    bob = Student(
        user_id=student_user_bob.id,
        student_id="UGR/0002/15",
        full_name="Bob Demoo",
        current_semester=1,
        department="Software Engineering",
        sponsorship_type=SponsorshipType.GOVERNMENT,
        enrollment_status=EnrollmentStatus.ACTIVE,
    )
    async_session.add_all([alice, bob])
    await async_session.flush()

    # ── Registrations ──
    for student, section, term in (
        (alice, s1, term1), (alice, s2, term2), (bob, s1, term1),
    ):
        async_session.add(Registration(
            student_id=student.id, term_id=term.id,
            status=RegistrationStatus.REGISTERED,
            sponsorship_type=SponsorshipType.GOVERNMENT,
            section_id=section.id,
            finalised_at=datetime.now(timezone.utc),
        ))
    await async_session.flush()

    # ── Grades ──
    # Alice term1: B B B → PROMOTED
    # Alice term2: F F F → DISMISSED (will be proposed but not authorised)
    # Bob term1: A+ A+ A → PROMOTED (but no standing computed yet)
    grades_map = [
        (alice, cs101, term1, s1, GradeLetter.B),
        (alice, cs102, term1, s1, GradeLetter.B),
        (alice, math101, term1, s1, GradeLetter.B),
        (alice, cs101, term2, s2, GradeLetter.F),
        (alice, cs102, term2, s2, GradeLetter.F),
        (alice, math101, term2, s2, GradeLetter.F),
        (bob, cs101, term1, s1, GradeLetter.A_PLUS),
        (bob, cs102, term1, s1, GradeLetter.A_PLUS),
        (bob, math101, term1, s1, GradeLetter.A),
    ]
    for student, course, term, _section, letter in grades_map:
        pts = points_for(letter)
        async_session.add(Grade(
            student_id=student.id, course_id=course.id, term_id=term.id,
            section_id=_section.id,
            letter_grade=letter,
            credit_hours=course.credit_hours,
            grade_points=(pts or 0.0) * course.credit_hours,
            status=GradeSubmissionStatus.AUTHORISED,
            entered_at=datetime.now(timezone.utc),
            authorised_at=datetime.now(timezone.utc),
        ))
    await async_session.flush()

    # ── Compute + authorise Alice's term 1 standing only ──
    svc = StandingService(async_session)
    await svc.compute_term_standing(user_id=dh_user.id, term_id=term1.id)
    await svc.compute_term_standing(user_id=dh_user.id, term_id=term2.id)

    alice_t1_standing = (await async_session.execute(
        select(AcademicStanding).where(
            AcademicStanding.student_id == alice.id,
            AcademicStanding.term_id == term1.id,
        )
    )).scalar_one()
    await svc.authorise(
        user_id=dh_user.id, standing_id=alice_t1_standing.id,
    )

    return {
        "term1": term1, "term2": term2,
        "alice": alice, "bob": bob,
        "alice_user": student_user_alice, "bob_user": student_user_bob,
        "cs101": cs101, "cs102": cs102, "math101": math101,
    }


# ── /me — multi-term ────────────────────────────────────────────


async def test_get_my_standings_only_includes_authorised_terms(
    async_session, student_scenario,
):
    """
    Alice has standings for both terms, but only term 1 is authorised.
    The student view must surface only term 1.
    """
    svc = StandingService(async_session)
    out = await svc.get_my_standings(
        user_id=student_scenario["alice_user"].id,
    )
    assert len(out.terms) == 1
    assert out.terms[0].term_id == student_scenario["term1"].id
    assert out.terms[0].status is AcademicStatusType.PROMOTED


async def test_get_my_standings_current_status_is_latest(
    async_session, student_scenario, dh_user,
):
    """Authorise term 2 (DISMISSED) → current_status is now DISMISSED."""
    svc = StandingService(async_session)
    alice = student_scenario["alice"]
    term2_standing = (await async_session.execute(
        select(AcademicStanding).where(
            AcademicStanding.student_id == alice.id,
            AcademicStanding.term_id == student_scenario["term2"].id,
        )
    )).scalar_one()
    await svc.authorise(user_id=dh_user.id, standing_id=term2_standing.id)

    out = await svc.get_my_standings(
        user_id=student_scenario["alice_user"].id,
    )
    assert len(out.terms) == 2
    # Newest first → term 2 (2024/2025) is first.
    assert out.terms[0].term_id == student_scenario["term2"].id
    assert out.current_status is AcademicStatusType.DISMISSED


async def test_get_my_standings_carries_explanation_from_history(
    async_session, student_scenario,
):
    """
    The student response carries the rule-engine reason captured at
    PROPOSED time as the explanation.
    """
    svc = StandingService(async_session)
    out = await svc.get_my_standings(
        user_id=student_scenario["alice_user"].id,
    )
    explanation = out.terms[0].explanation
    assert explanation is not None
    # Alice term 1 was promoted with clean Bs → reasoning should
    # mention SGPA / CGPA.
    assert "SGPA" in explanation or "CGPA" in explanation


async def test_get_my_standings_override_reason_takes_precedence(
    async_session, dh_user, student_scenario,
):
    """
    An overridden standing surfaces ``override_reason`` as the
    explanation, NOT the rule-engine sentence.
    """
    alice = student_scenario["alice"]
    term2_standing = (await async_session.execute(
        select(AcademicStanding).where(
            AcademicStanding.student_id == alice.id,
            AcademicStanding.term_id == student_scenario["term2"].id,
        )
    )).scalar_one()
    svc = StandingService(async_session)
    custom_reason = "Medical emergency documented mid-term."
    await svc.override(
        user_id=dh_user.id, standing_id=term2_standing.id,
        new_status=AcademicStatusType.WARNING,
        reason=custom_reason,
    )

    out = await svc.get_my_standings(
        user_id=student_scenario["alice_user"].id,
    )
    by_term = {t.term_id: t for t in out.terms}
    assert by_term[student_scenario["term2"].id].explanation == custom_reason


async def test_get_my_standings_empty_for_student_with_no_standings(
    async_session, student_scenario,
):
    """Bob has no computed standing → empty list, current_status None."""
    svc = StandingService(async_session)
    out = await svc.get_my_standings(
        user_id=student_scenario["bob_user"].id,
    )
    assert out.terms == []
    assert out.current_status is None


async def test_get_my_standings_rejects_non_student_user(
    async_session, dh_user,
):
    """Officer user has no Student row → 403."""
    svc = StandingService(async_session)
    with pytest.raises(StudentProfileRequiredError):
        await svc.get_my_standings(user_id=dh_user.id)


# ── /me/terms/{term_id} — single-term ──────────────────────────


async def test_get_my_term_standing_returns_authorised_row(
    async_session, student_scenario,
):
    svc = StandingService(async_session)
    out = await svc.get_my_term_standing(
        user_id=student_scenario["alice_user"].id,
        term_id=student_scenario["term1"].id,
    )
    assert out.term_id == student_scenario["term1"].id
    assert out.status is AcademicStatusType.PROMOTED
    assert out.sgpa == pytest.approx(3.0)


async def test_get_my_term_standing_404_for_unauthorised_term(
    async_session, student_scenario,
):
    """
    Alice's term 2 standing is computed but not authorised → 404.
    """
    svc = StandingService(async_session)
    with pytest.raises(EntityNotFoundError):
        await svc.get_my_term_standing(
            user_id=student_scenario["alice_user"].id,
            term_id=student_scenario["term2"].id,
        )


async def test_get_my_term_standing_404_for_unknown_term(
    async_session, student_scenario,
):
    svc = StandingService(async_session)
    with pytest.raises(EntityNotFoundError):
        await svc.get_my_term_standing(
            user_id=student_scenario["alice_user"].id,
            term_id=uuid.uuid4(),
        )


# ── Transcript bolt-on ─────────────────────────────────────────


async def test_transcript_carries_authorised_academic_status(
    async_session, student_scenario,
):
    """
    Alice's transcript has term 1 (authorised PROMOTED) AND term 2
    (grades exist but standing not authorised). The transcript
    must surface academic_status only on term 1.
    """
    svc = StudentTranscriptService(async_session)
    transcript = await svc.get_transcript(
        user_id=student_scenario["alice_user"].id,
    )
    by_term = {t.term_id: t for t in transcript.terms}

    term1 = by_term[student_scenario["term1"].id]
    assert term1.academic_status is AcademicStatusType.PROMOTED
    assert term1.academic_status_authorised_at is not None

    term2 = by_term[student_scenario["term2"].id]
    assert term2.academic_status is None
    assert term2.academic_status_authorised_at is None


async def test_get_term_grades_carries_authorised_status_for_one_term(
    async_session, student_scenario,
):
    svc = StudentTranscriptService(async_session)
    entry = await svc.get_term_grades(
        user_id=student_scenario["alice_user"].id,
        term_id=student_scenario["term1"].id,
    )
    assert entry.academic_status is AcademicStatusType.PROMOTED
    assert entry.academic_status_authorised_at is not None


async def test_get_term_grades_null_status_for_term_without_authorisation(
    async_session, student_scenario,
):
    svc = StudentTranscriptService(async_session)
    entry = await svc.get_term_grades(
        user_id=student_scenario["alice_user"].id,
        term_id=student_scenario["term2"].id,
    )
    # Grades exist (Fs), standing is computed but not authorised.
    assert entry.academic_status is None
    assert entry.academic_status_authorised_at is None


# ── Historical CGPA correctness regression guard ───────────────


async def test_past_term_standing_excludes_future_term_grades_from_cgpa(
    async_session, dh_user, student_scenario,
):
    """
    Regression guard: when standing is computed (or re-computed) for
    a past term, the CGPA must NOT include grades from later terms.

    Alice's term1 grades are all Bs (SGPA 3.0). Her term2 grades are
    all Fs. The term1 standing should see a clean CGPA of 3.0 — NOT
    polluted by the term2 Fs.
    """
    alice = student_scenario["alice"]
    term1_standing = (await async_session.execute(
        select(AcademicStanding).where(
            AcademicStanding.student_id == alice.id,
            AcademicStanding.term_id == student_scenario["term1"].id,
        )
    )).scalar_one()
    assert term1_standing.sgpa == pytest.approx(3.0)
    assert term1_standing.cgpa == pytest.approx(3.0), (
        f"CGPA leaked future-term grades: got {term1_standing.cgpa}"
    )
    # Verdict should be PROMOTED, not a Dismissal driven by 91.7.6.
    assert term1_standing.proposed_status is AcademicStatusType.PROMOTED
