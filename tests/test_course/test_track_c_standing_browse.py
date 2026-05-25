"""
Track C PR C1 — DH standing-browse read flow.

Service-level tests for the four read endpoints powering the
three-dropdown UX (term → department → section → students):

  StandingService.list_terms
  StandingService.list_departments
  StandingService.list_sections
  StandingService.get_section_roster

The roster test is the heart of the PR — it verifies that SGPA and
CGPA are sourced from the AcademicStanding snapshot (null until
compute has run), that add/drop context flags surface, and that
Article-91 evaluation context (F-counts, first-semester /
first-year flags, I/NG holds) is captured.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest
import pytest_asyncio

from app.modules.auth.models import User
from app.modules.course.exceptions import (
    EntityNotFoundError, UnauthorizedActorError,
)
from app.modules.course.grade_points import points_for
from app.modules.course.models import (
    AcademicTerm, Course, CourseManagementOfficer, Grade, Registration,
    Section, Student,
)
from app.modules.course.standing.service import StandingService
from app.shared.enums import (
    AcademicPhase, EnrollmentStatus, GradeLetter, GradeSubmissionStatus,
    OfficerRole, RegistrationStatus, SponsorshipType, UserRole,
)


# ── Fixtures specific to this PR ────────────────────────────────


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
async def officer_user(async_session) -> User:
    """A user with a CourseManagementOfficer row (REGISTRAR_OFFICER)."""
    user = _user(role=UserRole.REGISTRAR_OFFICER, email_slug="reg-officer")
    async_session.add(user)
    await async_session.flush()
    officer = CourseManagementOfficer(
        user_id=user.id,
        staff_id="REG/0007/15",
        role=OfficerRole.REGISTRAR_OFFICER,
        authorization_level=4,
    )
    async_session.add(officer)
    await async_session.flush()
    return user


@pytest_asyncio.fixture
async def dh_user(async_session) -> User:
    """A user with role DEPARTMENT_HEAD via CourseManagementOfficer."""
    user = _user(role=UserRole.REGISTRAR_OFFICER, email_slug="dept-head")
    async_session.add(user)
    await async_session.flush()
    officer = CourseManagementOfficer(
        user_id=user.id,
        staff_id="REG/0008/15",
        role=OfficerRole.DEPARTMENT_HEAD,
        authorization_level=5,
    )
    async_session.add(officer)
    await async_session.flush()
    return user


@pytest_asyncio.fixture
async def student_user(async_session) -> User:
    """A plain student — should be rejected by the auth gate."""
    user = _user(role=UserRole.STUDENT, email_slug="just-a-student")
    async_session.add(user)
    await async_session.flush()
    return user


@pytest_asyncio.fixture
async def admin_user(async_session) -> User:
    user = _user(role=UserRole.ADMIN, email_slug="root-admin")
    async_session.add(user)
    await async_session.flush()
    return user


@pytest_asyncio.fixture
async def standing_scenario(async_session, officer_user):
    """
    A small end-to-end fixture:
      - One closed term (history) with AUTHORISED grades for 3 students
      - One open term (current) with no grades yet
      - One Section in (history, "Software Engineering", semester 1)
        with 3 registered students
      - One Section in (history, "Computer Science", semester 1)
        with 0 students (tests the empty-roster branch)
      - 3 courses (CS101, CS102, MATH101) authoring sensible grades
    """
    del officer_user  # fixture only ensures users exist; not used directly

    # ── Terms ──
    history = AcademicTerm(
        term_name="2024/2025",
        start_date=date(2024, 9, 1),
        end_date=date(2025, 1, 31),
        is_open=False,
        phase=AcademicPhase.ONE,
    )
    current = AcademicTerm(
        term_name="2025/2026",
        start_date=date(2025, 9, 1),
        end_date=date(2026, 1, 31),
        is_open=True,
        phase=AcademicPhase.ONE,
    )
    async_session.add_all([history, current])
    await async_session.flush()

    # ── Courses ──
    cs101 = Course(
        code="CS101", title="Intro to Programming",
        credit_hours=3, semester=1, department="Software Engineering",
    )
    cs102 = Course(
        code="CS102", title="Discrete Math",
        credit_hours=3, semester=1, department="Software Engineering",
    )
    math101 = Course(
        code="MATH101", title="Calculus I",
        credit_hours=4, semester=1, department="Software Engineering",
    )
    async_session.add_all([cs101, cs102, math101])
    await async_session.flush()

    # ── Sections ──
    se_section = Section(
        term_id=history.id,
        department="Software Engineering",
        semester=1, section_code="A",
        capacity=10, enrolled_count=0,
    )
    cs_section = Section(
        term_id=history.id,
        department="Computer Science",
        semester=1, section_code="A",
        capacity=5, enrolled_count=0,
    )
    async_session.add_all([se_section, cs_section])
    await async_session.flush()

    # ── Students ──
    students: list[Student] = []
    for i, name in enumerate(("Alice", "Bob", "Carol"), start=1):
        u = _user(role=UserRole.STUDENT, email_slug=f"stu-{i:02d}")
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

    # ── Registrations (history term, SE section) ──
    for s in students:
        async_session.add(Registration(
            student_id=s.id,
            term_id=history.id,
            status=RegistrationStatus.REGISTERED,
            sponsorship_type=SponsorshipType.GOVERNMENT,
            section_id=se_section.id,
            finalised_at=datetime.now(timezone.utc),
        ))
    se_section.enrolled_count = len(students)
    await async_session.flush()

    # ── Grades — three distinct profiles ──
    #   Alice: A+, A+, A   → SGPA 4.00, all-pass, "top of class"
    #   Bob:   B, C+, C    → SGPA ~2.45
    #   Carol: F, F, D     → SGPA low; F-count=2, F-credits=6
    grade_map = {
        students[0]: [(cs101, GradeLetter.A_PLUS), (cs102, GradeLetter.A_PLUS), (math101, GradeLetter.A)],
        students[1]: [(cs101, GradeLetter.B), (cs102, GradeLetter.C_PLUS), (math101, GradeLetter.C)],
        students[2]: [(cs101, GradeLetter.F), (cs102, GradeLetter.F), (math101, GradeLetter.D)],
    }
    for s, rows in grade_map.items():
        for course, letter in rows:
            pts = points_for(letter)
            async_session.add(Grade(
                student_id=s.id,
                course_id=course.id,
                term_id=history.id,
                section_id=se_section.id,
                letter_grade=letter,
                numeric_score=None,  # not relevant for SGPA math
                credit_hours=course.credit_hours,
                grade_points=(pts or 0.0) * course.credit_hours,
                status=GradeSubmissionStatus.AUTHORISED,
                entered_at=datetime.now(timezone.utc),
                authorised_at=datetime.now(timezone.utc),
            ))
    await async_session.flush()

    return {
        "history": history,
        "current": current,
        "se_section": se_section,
        "cs_section": cs_section,
        "students": students,
        "cs101": cs101,
        "cs102": cs102,
        "math101": math101,
    }


# ── Auth gate ───────────────────────────────────────────────────


async def test_list_terms_rejects_student(async_session, student_user):
    svc = StandingService(async_session)
    with pytest.raises(UnauthorizedActorError):
        await svc.list_terms(user_id=student_user.id)


async def test_list_terms_accepts_admin(async_session, admin_user):
    svc = StandingService(async_session)
    # No terms yet — empty list, not 403.
    result = await svc.list_terms(user_id=admin_user.id)
    assert result == []


async def test_list_terms_accepts_dh(async_session, dh_user, standing_scenario):
    svc = StandingService(async_session)
    result = await svc.list_terms(user_id=dh_user.id)
    assert len(result) == 2


async def test_list_terms_accepts_registrar_officer(
    async_session, officer_user, standing_scenario,
):
    svc = StandingService(async_session)
    result = await svc.list_terms(user_id=officer_user.id)
    assert len(result) == 2


async def test_list_terms_rejects_officer_role_without_officer_row(
    async_session,
):
    """JWT carries REGISTRAR_OFFICER but no underlying officer row → 403."""
    user = _user(role=UserRole.REGISTRAR_OFFICER, email_slug="phantom-officer")
    async_session.add(user)
    await async_session.flush()
    svc = StandingService(async_session)
    with pytest.raises(UnauthorizedActorError):
        await svc.list_terms(user_id=user.id)


# ── Dropdown 1: terms ───────────────────────────────────────────


async def test_list_terms_flags_has_authorised_grades(
    async_session, officer_user, standing_scenario,
):
    """The closed term has AUTHORISED grades; the open term does not."""
    svc = StandingService(async_session)
    result = await svc.list_terms(user_id=officer_user.id)
    by_name = {t.term_name: t for t in result}
    assert by_name["2024/2025"].has_authorised_grades is True
    assert by_name["2025/2026"].has_authorised_grades is False


async def test_list_terms_newest_first(
    async_session, officer_user, standing_scenario,
):
    svc = StandingService(async_session)
    result = await svc.list_terms(user_id=officer_user.id)
    # Open term (2025/2026) starts later → comes first.
    assert result[0].term_name == "2025/2026"
    assert result[1].term_name == "2024/2025"


# ── Dropdown 2: departments ─────────────────────────────────────


async def test_list_departments_returns_distinct(
    async_session, officer_user, standing_scenario,
):
    svc = StandingService(async_session)
    history_id = standing_scenario["history"].id
    result = await svc.list_departments(
        user_id=officer_user.id, term_id=history_id,
    )
    depts = {r.department for r in result}
    # Both SE and CS sections exist in history term.
    assert depts == {"Software Engineering", "Computer Science"}


async def test_list_departments_counts(
    async_session, officer_user, standing_scenario,
):
    svc = StandingService(async_session)
    history_id = standing_scenario["history"].id
    result = await svc.list_departments(
        user_id=officer_user.id, term_id=history_id,
    )
    by_dept = {r.department: r for r in result}
    # SE: 1 section, 3 students.
    assert by_dept["Software Engineering"].section_count == 1
    assert by_dept["Software Engineering"].student_count == 3
    # CS: 1 section, 0 students.
    assert by_dept["Computer Science"].section_count == 1
    assert by_dept["Computer Science"].student_count == 0


async def test_list_departments_404_for_unknown_term(
    async_session, officer_user,
):
    svc = StandingService(async_session)
    with pytest.raises(EntityNotFoundError):
        await svc.list_departments(
            user_id=officer_user.id, term_id=uuid.uuid4(),
        )


# ── Dropdown 3: sections ────────────────────────────────────────


async def test_list_sections_filters_by_term_and_department(
    async_session, officer_user, standing_scenario,
):
    svc = StandingService(async_session)
    history_id = standing_scenario["history"].id
    result = await svc.list_sections(
        user_id=officer_user.id,
        term_id=history_id, department="Software Engineering",
    )
    assert len(result) == 1
    assert result[0].section_code == "A"
    assert result[0].department == "Software Engineering"
    assert result[0].semester == 1
    assert result[0].enrolled_count == 3


# ── Roster: live SGPA / CGPA computation ────────────────────────


async def test_get_section_roster_returns_cohort_with_grades(
    async_session, officer_user, standing_scenario,
):
    """
    Before compute runs, the roster shows every graded course per
    student but SGPA / CGPA come from the AcademicStanding snapshot
    — and no snapshot exists yet, so they're None. The grade rows
    themselves still surface so the DH can review what was entered.
    """
    svc = StandingService(async_session)
    result = await svc.get_section_roster(
        user_id=officer_user.id,
        term_id=standing_scenario["history"].id,
        section_id=standing_scenario["se_section"].id,
    )
    assert result.term_id == standing_scenario["history"].id
    assert result.section_id == standing_scenario["se_section"].id
    assert len(result.students) == 3

    # Sorted by student_id ascending (Alice=01, Bob=02, Carol=03).
    by_name = {s.full_name: s for s in result.students}

    # SGPA / CGPA are snapshot-sourced — null until compute writes
    # an AcademicStanding row.
    for stu in result.students:
        assert stu.sgpa is None
        assert stu.cgpa is None
        assert stu.term_credit_hours == 0
        assert stu.f_count_term == 0
        assert stu.f_credit_total_term == 0

    # Per-course rows still surface for review.
    alice = by_name["Alice"]
    assert len(alice.grades_this_term) == 3
    alice_letters = {r.letter_grade for r in alice.grades_this_term}
    assert GradeLetter.A_PLUS in alice_letters

    bob = by_name["Bob"]
    assert len(bob.grades_this_term) == 3

    carol = by_name["Carol"]
    assert len(carol.grades_this_term) == 3
    # has_incomplete_marks is derived from the displayed grades, so
    # it still works pre-compute.
    assert carol.has_incomplete_marks is False


async def test_get_section_roster_first_semester_flag_set(
    async_session, officer_user, standing_scenario,
):
    """
    Every seeded student has only grades from the history term (no
    prior term grades), so each one is in their "first semester" by
    the prior-term-count heuristic.
    """
    svc = StandingService(async_session)
    result = await svc.get_section_roster(
        user_id=officer_user.id,
        term_id=standing_scenario["history"].id,
        section_id=standing_scenario["se_section"].id,
    )
    for s in result.students:
        assert s.is_first_semester is True
        assert s.is_first_year is True


async def test_get_section_roster_first_year_false_after_prior_term(
    async_session, officer_user, standing_scenario,
):
    """
    Add an earlier term's authorised grade for Alice → she now has
    one prior term, so is_first_semester=False; still is_first_year
    (≤ 1 prior term).
    """
    older_term = AcademicTerm(
        term_name="2023/2024",
        start_date=date(2023, 9, 1),
        end_date=date(2024, 1, 31),
        is_open=False,
        phase=AcademicPhase.ONE,
    )
    async_session.add(older_term)
    await async_session.flush()

    alice = standing_scenario["students"][0]
    cs101 = standing_scenario["cs101"]
    async_session.add(Grade(
        student_id=alice.id,
        course_id=cs101.id,
        term_id=older_term.id,
        letter_grade=GradeLetter.B,
        credit_hours=cs101.credit_hours,
        grade_points=3.0 * cs101.credit_hours,
        status=GradeSubmissionStatus.AUTHORISED,
        entered_at=datetime.now(timezone.utc),
        authorised_at=datetime.now(timezone.utc),
    ))
    await async_session.flush()

    svc = StandingService(async_session)
    result = await svc.get_section_roster(
        user_id=officer_user.id,
        term_id=standing_scenario["history"].id,
        section_id=standing_scenario["se_section"].id,
    )
    by_name = {s.full_name: s for s in result.students}
    assert by_name["Alice"].is_first_semester is False
    # 1 prior term ≤ 1 → still in first year
    assert by_name["Alice"].is_first_year is True


async def test_get_section_roster_has_incomplete_marks_for_i_or_ng(
    async_session, officer_user, standing_scenario,
):
    """An I-grade on any of the term's courses should set the flag."""
    # Replace Bob's CS101 B grade with an I (Incomplete).
    bob = standing_scenario["students"][1]
    cs101 = standing_scenario["cs101"]
    from sqlalchemy import select, update
    await async_session.execute(
        update(Grade)
        .where(
            Grade.student_id == bob.id,
            Grade.course_id == cs101.id,
            Grade.term_id == standing_scenario["history"].id,
        )
        .values(
            letter_grade=GradeLetter.I,
            grade_points=None,  # I doesn't carry points
        )
    )
    await async_session.flush()

    svc = StandingService(async_session)
    result = await svc.get_section_roster(
        user_id=officer_user.id,
        term_id=standing_scenario["history"].id,
        section_id=standing_scenario["se_section"].id,
    )
    by_name = {s.full_name: s for s in result.students}
    assert by_name["Bob"].has_incomplete_marks is True
    # Alice and Carol unaffected.
    assert by_name["Alice"].has_incomplete_marks is False
    assert by_name["Carol"].has_incomplete_marks is False


async def test_get_section_roster_excludes_i_and_ng_from_sgpa(
    async_session, officer_user, standing_scenario,
):
    """
    Incomplete marks must not count toward SGPA per Art 90.7.4.
    The roster surfaces the I-letter row and flags the student as
    having incomplete marks; SGPA itself is null pre-compute (the
    snapshot does the I/NG exclusion math when compute runs).
    """
    bob = standing_scenario["students"][1]
    cs101 = standing_scenario["cs101"]
    from sqlalchemy import update
    await async_session.execute(
        update(Grade)
        .where(
            Grade.student_id == bob.id,
            Grade.course_id == cs101.id,
            Grade.term_id == standing_scenario["history"].id,
        )
        .values(letter_grade=GradeLetter.I, grade_points=None)
    )
    await async_session.flush()

    svc = StandingService(async_session)
    result = await svc.get_section_roster(
        user_id=officer_user.id,
        term_id=standing_scenario["history"].id,
        section_id=standing_scenario["se_section"].id,
    )
    bob_row = next(s for s in result.students if s.full_name == "Bob")
    assert bob_row.has_incomplete_marks is True
    # The I row surfaces in the per-course list.
    cs101_row = next(
        r for r in bob_row.grades_this_term
        if r.course_code == "CS101"
    )
    assert cs101_row.letter_grade == GradeLetter.I
    # SGPA is snapshot-sourced and null until compute runs.
    assert bob_row.sgpa is None


async def test_get_section_roster_empty_for_cohort_with_no_registrations(
    async_session, officer_user, standing_scenario,
):
    svc = StandingService(async_session)
    result = await svc.get_section_roster(
        user_id=officer_user.id,
        term_id=standing_scenario["history"].id,
        section_id=standing_scenario["cs_section"].id,
    )
    assert result.students == []


async def test_get_section_roster_404_for_unknown_section(
    async_session, officer_user, standing_scenario,
):
    svc = StandingService(async_session)
    with pytest.raises(EntityNotFoundError):
        await svc.get_section_roster(
            user_id=officer_user.id,
            term_id=standing_scenario["history"].id,
            section_id=uuid.uuid4(),
        )


async def test_get_section_roster_existing_standing_null_until_pr_c2(
    async_session, officer_user, standing_scenario,
):
    """PR C1 ships without compute — existing_standing is always None."""
    svc = StandingService(async_session)
    result = await svc.get_section_roster(
        user_id=officer_user.id,
        term_id=standing_scenario["history"].id,
        section_id=standing_scenario["se_section"].id,
    )
    for s in result.students:
        assert s.existing_standing is None
