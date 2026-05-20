"""
Track C PR C4 — Records (grade report + filing slip) tests.

Covers:
  * Grade report: surfaces the authorised AcademicStanding +
    per-course grades + cumulative GPA. 404 when the standing
    hasn't been authorised yet.
  * Filing slip: surfaces the Registration + RegistrationCourse +
    carry-over standing. 404 when no registration exists for the
    term.
  * Auth: student-only (officer / admin gets 403).
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
from app.modules.course.models import (
    AcademicTerm, Course, CourseManagementOfficer, Grade, Registration,
    RegistrationCourse, Section, Student,
)
from app.modules.course.records.service import StudentRecordsService
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
        first_name="Test", last_name="User",
        hashed_password="not-a-real-hash",
        role=role, is_active=True,
    )


@pytest_asyncio.fixture
async def dh_user(async_session) -> User:
    user = _user(role=UserRole.REGISTRAR_OFFICER, email_slug="dh-records")
    async_session.add(user)
    await async_session.flush()
    officer = CourseManagementOfficer(
        user_id=user.id, staff_id="REG/0990/15",
        role=OfficerRole.DEPARTMENT_HEAD, authorization_level=5,
    )
    async_session.add(officer)
    await async_session.flush()
    return user


@pytest_asyncio.fixture
async def records_scenario(async_session, dh_user):
    """
    Two-term arc for one student (Alice):
      term1 — completed, grades AUTHORISED, standing AUTHORISED
      term2 — currently registered, no grades, no standing yet
    """
    term1 = AcademicTerm(
        term_name="2023/2024",
        start_date=date(2023, 9, 1), end_date=date(2024, 1, 31),
        is_open=False, phase=AcademicPhase.ONE,
    )
    term2 = AcademicTerm(
        term_name="2024/2025",
        start_date=date(2024, 9, 1), end_date=date(2025, 1, 31),
        is_open=True, phase=AcademicPhase.ONE,
    )
    async_session.add_all([term1, term2])
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

    s1 = Section(
        term_id=term1.id, department="Software Engineering",
        semester=1, section_code="A", capacity=10, enrolled_count=0,
    )
    s2 = Section(
        term_id=term2.id, department="Software Engineering",
        semester=2, section_code="A", capacity=10, enrolled_count=0,
    )
    async_session.add_all([s1, s2])
    await async_session.flush()

    student_user = _user(role=UserRole.STUDENT, email_slug="alice-records")
    async_session.add(student_user)
    await async_session.flush()
    alice = Student(
        user_id=student_user.id,
        student_id="UGR/0001/15", full_name="Alice Records",
        current_semester=2,
        department="Software Engineering",
        sponsorship_type=SponsorshipType.GOVERNMENT,
        enrollment_status=EnrollmentStatus.ACTIVE,
    )
    async_session.add(alice)
    await async_session.flush()

    # Registration: term1 (completed) + term2 (active)
    reg_term1 = Registration(
        student_id=alice.id, term_id=term1.id,
        status=RegistrationStatus.REGISTERED,
        sponsorship_type=SponsorshipType.GOVERNMENT,
        section_id=s1.id,
        finalised_at=datetime.now(timezone.utc),
    )
    reg_term2 = Registration(
        student_id=alice.id, term_id=term2.id,
        status=RegistrationStatus.REGISTERED,
        sponsorship_type=SponsorshipType.GOVERNMENT,
        section_id=s2.id,
        payment_reference="MOCK-PAY-001",
        finalised_at=datetime.now(timezone.utc),
    )
    async_session.add_all([reg_term1, reg_term2])
    await async_session.flush()

    # Term2 registered courses (cs101 + cs102; math101 was dropped)
    async_session.add(RegistrationCourse(
        registration_id=reg_term2.id, course_id=cs101.id, is_dropped=False,
    ))
    async_session.add(RegistrationCourse(
        registration_id=reg_term2.id, course_id=cs102.id, is_dropped=False,
    ))
    async_session.add(RegistrationCourse(
        registration_id=reg_term2.id, course_id=math101.id, is_dropped=True,
    ))
    await async_session.flush()

    # Term1 grades — clean Bs.
    for course in (cs101, cs102, math101):
        pts = points_for(GradeLetter.B)
        async_session.add(Grade(
            student_id=alice.id, course_id=course.id, term_id=term1.id,
            section_id=s1.id,
            letter_grade=GradeLetter.B,
            credit_hours=course.credit_hours,
            grade_points=(pts or 0.0) * course.credit_hours,
            status=GradeSubmissionStatus.AUTHORISED,
            entered_at=datetime.now(timezone.utc),
            authorised_at=datetime.now(timezone.utc),
        ))
    await async_session.flush()

    # Compute + authorise term1 standing.
    svc = StandingService(async_session)
    await svc.compute_term_standing(user_id=dh_user.id, term_id=term1.id)
    standing = (await async_session.execute(
        select(AcademicStanding).where(
            AcademicStanding.student_id == alice.id,
            AcademicStanding.term_id == term1.id,
        )
    )).scalar_one()
    await svc.authorise(user_id=dh_user.id, standing_id=standing.id)

    return {
        "term1": term1, "term2": term2,
        "alice": alice, "alice_user": student_user,
        "cs101": cs101, "cs102": cs102, "math101": math101,
        "reg_term2": reg_term2,
    }


# ── Grade report ────────────────────────────────────────────────


async def test_grade_report_returns_authorised_term_payload(
    async_session, records_scenario,
):
    svc = StudentRecordsService(async_session)
    report = await svc.get_grade_report(
        user_id=records_scenario["alice_user"].id,
        term_id=records_scenario["term1"].id,
    )
    assert report.student.student_number == "UGR/0001/15"
    assert report.student.full_name == "Alice Records"
    assert report.term.term_name == "2023/2024"
    assert report.academic_status is AcademicStatusType.PROMOTED
    assert report.term_gpa == pytest.approx(3.0)
    assert report.cumulative_gpa == pytest.approx(3.0)
    assert len(report.courses) == 3
    course_codes = {c.course_code for c in report.courses}
    assert course_codes == {"CS101", "CS102", "MATH101"}


async def test_grade_report_404_when_standing_not_authorised(
    async_session, records_scenario,
):
    """Term 2 has no standing computed yet → 404."""
    svc = StudentRecordsService(async_session)
    with pytest.raises(EntityNotFoundError):
        await svc.get_grade_report(
            user_id=records_scenario["alice_user"].id,
            term_id=records_scenario["term2"].id,
        )


async def test_grade_report_404_for_unknown_term(
    async_session, records_scenario,
):
    svc = StudentRecordsService(async_session)
    with pytest.raises(EntityNotFoundError):
        await svc.get_grade_report(
            user_id=records_scenario["alice_user"].id,
            term_id=uuid.uuid4(),
        )


async def test_grade_report_rejects_non_student_caller(
    async_session, dh_user, records_scenario,
):
    """The DH user has no Student row → 403."""
    svc = StudentRecordsService(async_session)
    with pytest.raises(StudentProfileRequiredError):
        await svc.get_grade_report(
            user_id=dh_user.id,
            term_id=records_scenario["term1"].id,
        )


async def test_grade_report_courses_sorted_by_code(
    async_session, records_scenario,
):
    svc = StudentRecordsService(async_session)
    report = await svc.get_grade_report(
        user_id=records_scenario["alice_user"].id,
        term_id=records_scenario["term1"].id,
    )
    codes = [c.course_code for c in report.courses]
    assert codes == sorted(codes)


# ── Filing slip ─────────────────────────────────────────────────


async def test_filing_slip_returns_registered_courses(
    async_session, records_scenario,
):
    svc = StudentRecordsService(async_session)
    slip = await svc.get_filing_slip(
        user_id=records_scenario["alice_user"].id,
        term_id=records_scenario["term2"].id,
    )
    assert slip.student.full_name == "Alice Records"
    assert slip.term.term_name == "2024/2025"
    assert slip.registration_status is RegistrationStatus.REGISTERED
    assert slip.section_code == "A"
    assert len(slip.courses) == 3  # cs101, cs102, math101 (one dropped)
    # Active credit hours sum to CS101 (3) + CS102 (3) = 6; math101 dropped.
    assert slip.total_credit_hours == 6


async def test_filing_slip_marks_dropped_courses(
    async_session, records_scenario,
):
    svc = StudentRecordsService(async_session)
    slip = await svc.get_filing_slip(
        user_id=records_scenario["alice_user"].id,
        term_id=records_scenario["term2"].id,
    )
    by_code = {c.course_code: c for c in slip.courses}
    assert by_code["MATH101"].is_dropped is True
    assert by_code["CS101"].is_dropped is False


async def test_filing_slip_carries_payment_reference(
    async_session, records_scenario,
):
    svc = StudentRecordsService(async_session)
    slip = await svc.get_filing_slip(
        user_id=records_scenario["alice_user"].id,
        term_id=records_scenario["term2"].id,
    )
    assert slip.payment_reference == "MOCK-PAY-001"


async def test_filing_slip_carries_previous_authorised_standing(
    async_session, records_scenario,
):
    """
    Alice's term 2 filing slip should carry over her term 1
    PROMOTED status as ``last_authorised_status``.
    """
    svc = StudentRecordsService(async_session)
    slip = await svc.get_filing_slip(
        user_id=records_scenario["alice_user"].id,
        term_id=records_scenario["term2"].id,
    )
    assert slip.last_authorised_status is AcademicStatusType.PROMOTED
    assert slip.last_authorised_term_name == "2023/2024"


async def test_filing_slip_404_when_no_registration_exists(
    async_session, records_scenario,
):
    """A term with no registration → 404."""
    standalone_term = AcademicTerm(
        term_name="2022/2023",
        start_date=date(2022, 9, 1), end_date=date(2023, 1, 31),
        is_open=False, phase=AcademicPhase.ONE,
    )
    async_session.add(standalone_term)
    await async_session.flush()

    svc = StudentRecordsService(async_session)
    with pytest.raises(EntityNotFoundError):
        await svc.get_filing_slip(
            user_id=records_scenario["alice_user"].id,
            term_id=standalone_term.id,
        )


async def test_filing_slip_rejects_non_student_caller(
    async_session, dh_user, records_scenario,
):
    svc = StudentRecordsService(async_session)
    with pytest.raises(StudentProfileRequiredError):
        await svc.get_filing_slip(
            user_id=dh_user.id,
            term_id=records_scenario["term2"].id,
        )


# ── Cross-check: identity panels ───────────────────────────────


async def test_grade_report_and_filing_slip_share_student_header(
    async_session, records_scenario,
):
    svc = StudentRecordsService(async_session)
    report = await svc.get_grade_report(
        user_id=records_scenario["alice_user"].id,
        term_id=records_scenario["term1"].id,
    )
    slip = await svc.get_filing_slip(
        user_id=records_scenario["alice_user"].id,
        term_id=records_scenario["term2"].id,
    )
    assert report.student.student_id == slip.student.student_id
    assert report.student.full_name == slip.student.full_name
