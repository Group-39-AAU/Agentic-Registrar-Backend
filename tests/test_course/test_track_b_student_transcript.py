"""
Track B PR 4 — Student transcript view.

Only AUTHORISED grades surface. SUBMITTED / FLAGGED / REJECTED /
DRAFT grades are invisible to the student until the DH authorises.

The transcript includes per-component breakdown rows when a Track B
``GradeBatch`` backs the ``Grade`` row; legacy ``Grade`` rows
without a batch (e.g. data seeded before Track B) carry
``has_breakdown=False`` and an empty components list.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.modules.auth.models import User
from app.modules.course.exceptions import (
    EntityNotFoundError, StudentProfileRequiredError,
)
from app.modules.course.grading.agents import GradingMonitorAgent
from app.modules.course.grading.dh_service import (
    DepartmentHeadGradingService,
)
from app.modules.course.grading.schemas import (
    AssessmentBreakdownCreate, AssessmentComponentCreate,
    BulkScoreWrite, ScoreCellWrite,
)
from app.modules.course.grading.service import InstructorGradingService
from app.modules.course.grading.transcript_service import (
    StudentTranscriptService,
)
from app.modules.course.models import (
    AcademicTerm, ClassScheduleSlot, Course, CourseManagementOfficer, Grade,
    Instructor, Registration, RegistrationCourse, Section, Student,
)
from app.shared.enums import (
    AcademicPhase, EnrollmentStatus, GradeLetter, GradeSubmissionStatus,
    OfficerRole, RegistrationStatus, SponsorshipType, UserRole,
)


class _ApproveLLM:
    async def review_grade_batch_as_dh(self, review_payload):
        return {"verdict": "APPROVE", "flags": [], "reasoning": "ok"}


@pytest_asyncio.fixture
async def transcript_scenario(async_session):
    """
    A student with two AUTHORISED batches (across two terms), a
    SUBMITTED batch (still pending DH), and a single legacy
    AUTHORISED Grade row without a Track B batch.
    """
    # Two terms.
    fall_2026 = AcademicTerm(
        term_name="Fall-2026", phase=AcademicPhase.ONE,
        start_date=date(2026, 9, 1), end_date=date(2027, 1, 31),
        is_open=False,
    )
    spring_2027 = AcademicTerm(
        term_name="Spring-2027", phase=AcademicPhase.TWO,
        start_date=date(2027, 2, 1), end_date=date(2027, 6, 30),
        is_open=True,
    )
    cs101 = Course(
        code="CS101", title="Intro Programming",
        credit_hours=3, semester=1, department="Computer Science",
    )
    cs201 = Course(
        code="CS201", title="Data Structures",
        credit_hours=4, semester=2, department="Computer Science",
    )
    math101 = Course(
        code="MATH101", title="Calculus I",
        credit_hours=3, semester=1, department="Computer Science",
    )
    async_session.add_all([fall_2026, spring_2027, cs101, cs201, math101])
    await async_session.flush()

    # Instructor + DH.
    instr_user = User(
        id=uuid.uuid4(), email="lemma-pr4-tr@aau.edu.et",
        first_name="Lemma", last_name="Bekele",
        hashed_password="x", role=UserRole.INSTRUCTOR, is_active=True,
    )
    dh_user = User(
        id=uuid.uuid4(), email="dh-pr4-tr@aau.edu.et",
        first_name="Almaz", last_name="Tilahun",
        hashed_password="x", role=UserRole.REGISTRAR_OFFICER, is_active=True,
    )
    async_session.add_all([instr_user, dh_user])
    await async_session.flush()
    instructor = Instructor(
        user_id=instr_user.id, instructor_id="STAFF/TR4/15",
        department="Computer Science",
    )
    async_session.add(instructor)
    async_session.add(CourseManagementOfficer(
        user_id=dh_user.id, staff_id="REG/TR4/15",
        role=OfficerRole.DEPARTMENT_HEAD, authorization_level=5,
    ))
    await async_session.flush()

    # Sections + slots so the instructor teaches every (term, course).
    section_fall = Section(
        term_id=fall_2026.id, department="Computer Science",
        semester=1, section_code="A", capacity=30, enrolled_count=0,
    )
    section_spring = Section(
        term_id=spring_2027.id, department="Computer Science",
        semester=2, section_code="A", capacity=30, enrolled_count=0,
    )
    async_session.add_all([section_fall, section_spring])
    await async_session.flush()
    # ClassScheduleSlot is unique on (section, day, start_time); use
    # distinct hours per slot to avoid that constraint when two
    # courses share a section.
    slot_layout = [
        (section_fall, cs101, "MON", 9),
        (section_fall, math101, "MON", 11),
        (section_spring, cs201, "TUE", 9),
    ]
    for sec, course, day, hour in slot_layout:
        async_session.add(ClassScheduleSlot(
            section_id=sec.id, course_id=course.id,
            instructor_id=instructor.id,
            day_of_week=day,
            start_time=time(hour, 0), end_time=time(hour + 1, 0),
            room="LAB-1",
        ))
    await async_session.flush()

    # The student.
    student_user = User(
        id=uuid.uuid4(), email="abel-pr4-tr@aau.edu.et",
        first_name="Abel", last_name="Tesfaye",
        hashed_password="x", role=UserRole.STUDENT, is_active=True,
    )
    async_session.add(student_user)
    await async_session.flush()
    student = Student(
        user_id=student_user.id, student_id="UGR/0001/15",
        full_name="Abel Tesfaye", current_semester=2,
        department="Computer Science",
        sponsorship_type=SponsorshipType.GOVERNMENT,
        enrollment_status=EnrollmentStatus.ACTIVE,
    )
    async_session.add(student)
    await async_session.flush()

    # Per-term registrations + course rows.
    for term, section, course_set in [
        (fall_2026, section_fall, [cs101, math101]),
        (spring_2027, section_spring, [cs201]),
    ]:
        reg = Registration(
            student_id=student.id, term_id=term.id, section_id=section.id,
            status=RegistrationStatus.REGISTERED,
            sponsorship_type=SponsorshipType.GOVERNMENT,
        )
        async_session.add(reg)
        await async_session.flush()
        for c in course_set:
            async_session.add(RegistrationCourse(
                registration_id=reg.id, course_id=c.id, is_dropped=False,
            ))
        await async_session.flush()

    return {
        "fall_2026": fall_2026, "spring_2027": spring_2027,
        "cs101": cs101, "cs201": cs201, "math101": math101,
        "instructor": instructor, "instr_user": instr_user,
        "dh_user": dh_user,
        "section_fall": section_fall, "section_spring": section_spring,
        "student": student, "student_user": student_user,
    }


async def _submit_and_authorise(
    async_session, w, *, term, section, course, score_grid,
):
    """Helper: post breakdown + scores, submit, DH authorises."""
    svc = InstructorGradingService(
        async_session,
        grading_agent=GradingMonitorAgent(llm_client=_ApproveLLM()),
    )
    bd = await svc.upsert_breakdown(
        user_id=w["instr_user"].id,
        section_id=section.id, course_id=course.id,
        payload=AssessmentBreakdownCreate(components=[
            AssessmentComponentCreate(name="Mid",   weight=40, max_score=50),
            AssessmentComponentCreate(name="Final", weight=60, max_score=100),
        ]),
    )
    batch = await svc.get_or_create_batch(
        user_id=w["instr_user"].id,
        section_id=section.id, course_id=course.id,
    )
    mid_id = next(c.id for c in bd.components if c.name == "Mid")
    final_id = next(c.id for c in bd.components if c.name == "Final")
    student = w["student"]
    mid_score, final_score = score_grid
    await svc.upsert_scores(
        user_id=w["instr_user"].id, batch_id=batch.id,
        payload=BulkScoreWrite(cells=[
            ScoreCellWrite(student_id=student.id, component_id=mid_id, score=mid_score),
            ScoreCellWrite(student_id=student.id, component_id=final_id, score=final_score),
        ]),
    )
    await svc.submit_batch(user_id=w["instr_user"].id, batch_id=batch.id)
    dh = DepartmentHeadGradingService(async_session)
    await dh.authorise(user_id=w["dh_user"].id, batch_id=batch.id)
    return batch


# ── Auth gate ───────────────────────────────────────────────────


async def test_transcript_requires_student_profile(async_session):
    """A user with no Student row gets 403."""
    user = User(
        id=uuid.uuid4(), email="ghost-tr@aau.edu.et",
        first_name="Ghost", last_name="X",
        hashed_password="x", role=UserRole.STUDENT, is_active=True,
    )
    async_session.add(user)
    await async_session.flush()
    svc = StudentTranscriptService(async_session)
    with pytest.raises(StudentProfileRequiredError):
        await svc.get_transcript(user_id=user.id)


# ── Visibility ──────────────────────────────────────────────────


async def test_transcript_includes_only_authorised_grades(
    async_session, transcript_scenario,
):
    """SUBMITTED / FLAGGED / REJECTED grades never reach the student."""
    w = transcript_scenario

    # Authorise one course in fall_2026.
    await _submit_and_authorise(
        async_session, w,
        term=w["fall_2026"], section=w["section_fall"], course=w["cs101"],
        score_grid=(45, 90),
    )

    # Submit MATH101 but DON'T authorise → student must not see it.
    svc = InstructorGradingService(
        async_session,
        grading_agent=GradingMonitorAgent(llm_client=_ApproveLLM()),
    )
    bd = await svc.upsert_breakdown(
        user_id=w["instr_user"].id,
        section_id=w["section_fall"].id, course_id=w["math101"].id,
        payload=AssessmentBreakdownCreate(components=[
            AssessmentComponentCreate(name="Final", weight=100, max_score=100),
        ]),
    )
    batch = await svc.get_or_create_batch(
        user_id=w["instr_user"].id,
        section_id=w["section_fall"].id, course_id=w["math101"].id,
    )
    final_id = bd.components[0].id
    await svc.upsert_scores(
        user_id=w["instr_user"].id, batch_id=batch.id,
        payload=BulkScoreWrite(cells=[
            ScoreCellWrite(
                student_id=w["student"].id, component_id=final_id, score=80,
            ),
        ]),
    )
    await svc.submit_batch(user_id=w["instr_user"].id, batch_id=batch.id)
    # SUBMITTED but not authorised — invisible.

    transcript = await StudentTranscriptService(async_session).get_transcript(
        user_id=w["student_user"].id,
    )
    course_codes = {
        course.course_code
        for term in transcript.terms
        for course in term.courses
    }
    assert course_codes == {"CS101"}
    assert "MATH101" not in course_codes


# ── Breakdown surfaces in transcript ────────────────────────────


async def test_transcript_carries_breakdown_for_track_b_batches(
    async_session, transcript_scenario,
):
    w = transcript_scenario
    await _submit_and_authorise(
        async_session, w,
        term=w["fall_2026"], section=w["section_fall"], course=w["cs101"],
        score_grid=(45, 90),
    )
    transcript = await StudentTranscriptService(async_session).get_transcript(
        user_id=w["student_user"].id,
    )
    assert len(transcript.terms) == 1
    courses = transcript.terms[0].courses
    cs101 = next(c for c in courses if c.course_code == "CS101")
    assert cs101.has_breakdown is True
    names = {c.name for c in cs101.components}
    assert names == {"Mid", "Final"}
    # Mid: 45/50 × 40 = 36; Final: 90/100 × 60 = 54; total 90 → A-.
    mid = next(c for c in cs101.components if c.name == "Mid")
    final = next(c for c in cs101.components if c.name == "Final")
    assert mid.score == 45
    assert mid.weighted_contribution == 36.0
    assert final.score == 90
    assert final.weighted_contribution == 54.0
    assert cs101.numeric_score == 90.0
    # 90.0 is the lower boundary for A in the AAU scale (≥90 → A).
    assert cs101.letter_grade is GradeLetter.A


async def test_transcript_legacy_grade_without_batch(
    async_session, transcript_scenario,
):
    """A bare AUTHORISED Grade row (no batch) surfaces with has_breakdown=False."""
    w = transcript_scenario
    async_session.add(Grade(
        student_id=w["student"].id,
        course_id=w["cs201"].id,
        term_id=w["spring_2027"].id,
        section_id=w["section_spring"].id,
        letter_grade=GradeLetter.B,
        numeric_score=78.0,
        credit_hours=4,
        grade_points=12.0,
        status=GradeSubmissionStatus.AUTHORISED,
        entered_at=datetime.now(timezone.utc),
        authorised_at=datetime.now(timezone.utc),
    ))
    await async_session.flush()

    transcript = await StudentTranscriptService(async_session).get_transcript(
        user_id=w["student_user"].id,
    )
    spring = next(t for t in transcript.terms if t.term_name == "Spring-2027")
    cs201 = next(c for c in spring.courses if c.course_code == "CS201")
    assert cs201.has_breakdown is False
    assert cs201.components == []
    assert cs201.letter_grade is GradeLetter.B
    assert cs201.numeric_score == 78.0


# ── GPA math ────────────────────────────────────────────────────


async def test_transcript_term_and_cgpa(
    async_session, transcript_scenario,
):
    """Two authorised courses in one term + one in another → CGPA composite."""
    w = transcript_scenario

    # Fall: CS101 (3 cr) — Mid 45/50×40=36 + Final 90/100×60=54 → 90 → A
    #   grade_points = 3 × 4.00 = 12.0
    await _submit_and_authorise(
        async_session, w,
        term=w["fall_2026"], section=w["section_fall"], course=w["cs101"],
        score_grid=(45, 90),
    )
    # Fall: MATH101 (3 cr) — Mid 30/50×40=24 + Final 60/100×60=36 → 60 → C
    #   grade_points = 3 × 2.00 = 6.0
    await _submit_and_authorise(
        async_session, w,
        term=w["fall_2026"], section=w["section_fall"], course=w["math101"],
        score_grid=(30, 60),
    )
    # Spring: CS201 (4 cr) — Mid 30/50×40=24 + Final 85/100×60=51 → 75 → B
    #   grade_points = 4 × 3.00 = 12.0
    await _submit_and_authorise(
        async_session, w,
        term=w["spring_2027"], section=w["section_spring"], course=w["cs201"],
        score_grid=(30, 85),
    )

    transcript = await StudentTranscriptService(async_session).get_transcript(
        user_id=w["student_user"].id,
    )
    assert len(transcript.terms) == 2
    # Terms ordered newest-first.
    assert transcript.terms[0].term_name == "Spring-2027"
    assert transcript.terms[1].term_name == "Fall-2026"

    # Per-term GPAs.
    spring_gpa = transcript.terms[0].term_gpa
    fall_gpa = transcript.terms[1].term_gpa
    # Spring: 12.0 / 4 = 3.00
    assert spring_gpa == 3.00
    # Fall: (12.0 + 6.0) / 6 = 3.00
    assert fall_gpa == 3.00

    # CGPA: (12.0 + 6.0 + 12.0) / (3+3+4) = 30.0/10 = 3.00
    assert transcript.cgpa == 3.00
    assert transcript.total_credit_hours_completed == 10


# ── Per-term endpoint ──────────────────────────────────────────


async def test_get_term_grades_filters_by_term(
    async_session, transcript_scenario,
):
    w = transcript_scenario
    await _submit_and_authorise(
        async_session, w,
        term=w["fall_2026"], section=w["section_fall"], course=w["cs101"],
        score_grid=(45, 90),
    )
    await _submit_and_authorise(
        async_session, w,
        term=w["spring_2027"], section=w["section_spring"], course=w["cs201"],
        score_grid=(30, 85),
    )
    svc = StudentTranscriptService(async_session)
    fall = await svc.get_term_grades(
        user_id=w["student_user"].id, term_id=w["fall_2026"].id,
    )
    assert {c.course_code for c in fall.courses} == {"CS101"}


async def test_get_term_grades_404_for_unknown_term(
    async_session, transcript_scenario,
):
    w = transcript_scenario
    svc = StudentTranscriptService(async_session)
    with pytest.raises(EntityNotFoundError):
        await svc.get_term_grades(
            user_id=w["student_user"].id, term_id=uuid.uuid4(),
        )


async def test_empty_transcript_for_student_with_no_authorised_grades(
    async_session, transcript_scenario,
):
    """No AUTHORISED rows → empty terms + None CGPA."""
    w = transcript_scenario
    transcript = await StudentTranscriptService(async_session).get_transcript(
        user_id=w["student_user"].id,
    )
    assert transcript.terms == []
    assert transcript.cgpa is None
    assert transcript.total_credit_hours_completed == 0
