"""
Track B PR 2 — breakdown editor + grade-entry batch + submit.

Covers the policy invariants the spec calls out:

  * Component weights must sum to exactly 100. (122, 156 of checklist)
  * Component names within a breakdown must be unique.
  * After any score is saved, the breakdown locks for edits.
  * A grade batch is one-per-(section, course); GET-or-CREATE is idempotent.
  * Score upserts reject out-of-range and off-roster cells.
  * Submit refuses while any cell is missing, listing the gaps.
  * Submit computes weighted_pct + AAU letter per student and writes
    one ``Grade`` row per roster member at status=SUBMITTED.
  * Submit transitions the batch DRAFT → SUBMITTED via the stub agent.
"""
from __future__ import annotations

import uuid
from datetime import date, time

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.modules.auth.models import User
from app.modules.course.exceptions import (
    BreakdownLockedError, EntityNotFoundError,
    GradeBatchNotEditableError, IncompleteGradeSubmissionError,
    InvalidBreakdownError, UnauthorizedActorError,
)
from app.modules.course.grading.models import (
    AssessmentBreakdown, AssessmentComponent, GradeBatch,
    StudentComponentScore,
)
from app.modules.course.grading.schemas import (
    AssessmentBreakdownCreate, AssessmentComponentCreate,
    BulkScoreWrite, ScoreCellWrite,
)
from app.modules.course.grading.service import InstructorGradingService
from app.modules.course.models import (
    AcademicTerm, ClassScheduleSlot, Course, Grade, Instructor,
    Registration, RegistrationCourse, Section, Student,
)
from app.shared.enums import (
    AcademicPhase, EnrollmentStatus, GradeLetter, GradeSubmissionStatus,
    RegistrationStatus, SponsorshipType, UserRole,
)


# ── Shared fixture: 3-student grading scenario ──────────────────


@pytest_asyncio.fixture
async def grading_scenario(async_session):
    """
    Term + course + instructor + section + 3 originals on the roster.
    Returns a dict with all entities. PR 2 tests bring their own
    breakdown/batch/scores per test.
    """
    term = AcademicTerm(
        term_name="PR2-Test-2026",
        phase=AcademicPhase.ONE,
        start_date=date(2026, 9, 1),
        end_date=date(2027, 1, 31),
        is_open=True,
    )
    course = Course(
        code="CS101", title="Intro Programming",
        credit_hours=3, semester=1, department="Computer Science",
    )
    async_session.add_all([term, course])
    await async_session.flush()

    instr_user = User(
        id=uuid.uuid4(),
        email="lemma-pr2@aau.edu.et",
        first_name="Lemma", last_name="Bekele",
        hashed_password="x", role=UserRole.INSTRUCTOR, is_active=True,
    )
    async_session.add(instr_user)
    await async_session.flush()

    instructor = Instructor(
        user_id=instr_user.id,
        instructor_id="STAFF/PR2/15",
        department="Computer Science",
    )
    async_session.add(instructor)
    await async_session.flush()

    section = Section(
        term_id=term.id, department="Computer Science",
        semester=1, section_code="A", capacity=30, enrolled_count=0,
    )
    async_session.add(section)
    await async_session.flush()

    slot = ClassScheduleSlot(
        section_id=section.id, course_id=course.id,
        instructor_id=instructor.id,
        day_of_week="MON", start_time=time(9, 0), end_time=time(10, 0),
        room="LAB-1",
    )
    async_session.add(slot)
    await async_session.flush()

    students: list[Student] = []
    for sid, name in [
        ("UGR/0001/15", "Abel Tesfaye"),
        ("UGR/0002/15", "Bethel Demissie"),
        ("UGR/0003/15", "Chala Worku"),
    ]:
        user = User(
            id=uuid.uuid4(),
            email=f"{sid.lower().replace('/', '-')}-pr2@aau.edu.et",
            first_name=name.split()[0], last_name=name.split()[-1],
            hashed_password="x", role=UserRole.STUDENT, is_active=True,
        )
        async_session.add(user)
        await async_session.flush()
        student = Student(
            user_id=user.id, student_id=sid, full_name=name,
            current_semester=1, department="Computer Science",
            sponsorship_type=SponsorshipType.GOVERNMENT,
            enrollment_status=EnrollmentStatus.ACTIVE,
        )
        async_session.add(student)
        await async_session.flush()
        reg = Registration(
            student_id=student.id, term_id=term.id, section_id=section.id,
            status=RegistrationStatus.REGISTERED,
            sponsorship_type=SponsorshipType.GOVERNMENT,
        )
        async_session.add(reg)
        await async_session.flush()
        async_session.add(RegistrationCourse(
            registration_id=reg.id, course_id=course.id, is_dropped=False,
        ))
        await async_session.flush()
        students.append(student)

    return {
        "term": term, "course": course,
        "instructor": instructor, "instr_user": instr_user,
        "section": section, "slot": slot,
        "students": students,
    }


def _three_one_breakdown() -> AssessmentBreakdownCreate:
    """A canonical 30/30/40 breakdown the tests reuse."""
    return AssessmentBreakdownCreate(components=[
        AssessmentComponentCreate(name="Quiz",  weight=30, max_score=10),
        AssessmentComponentCreate(name="Mid",   weight=30, max_score=50),
        AssessmentComponentCreate(name="Final", weight=40, max_score=100),
    ])


# ── Breakdown invariant tests ───────────────────────────────────


async def test_breakdown_rejects_sum_not_100(async_session, grading_scenario):
    w = grading_scenario
    svc = InstructorGradingService(async_session)
    bad = AssessmentBreakdownCreate(components=[
        AssessmentComponentCreate(name="Mid",   weight=30),
        AssessmentComponentCreate(name="Final", weight=60),  # sums to 90
    ])
    with pytest.raises(InvalidBreakdownError):
        await svc.upsert_breakdown(
            user_id=w["instr_user"].id,
            section_id=w["section"].id, course_id=w["course"].id,
            payload=bad,
        )


async def test_breakdown_accepts_floating_sum_within_tolerance(
    async_session, grading_scenario,
):
    """33.33 + 33.33 + 33.34 = 100.0 exactly; floats might wobble."""
    w = grading_scenario
    svc = InstructorGradingService(async_session)
    payload = AssessmentBreakdownCreate(components=[
        AssessmentComponentCreate(name="A", weight=33.33),
        AssessmentComponentCreate(name="B", weight=33.33),
        AssessmentComponentCreate(name="C", weight=33.34),
    ])
    resp = await svc.upsert_breakdown(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
        payload=payload,
    )
    assert len(resp.components) == 3
    assert resp.version == 1


async def test_breakdown_rejects_duplicate_names(async_session, grading_scenario):
    w = grading_scenario
    svc = InstructorGradingService(async_session)
    bad = AssessmentBreakdownCreate(components=[
        AssessmentComponentCreate(name="Quiz", weight=50),
        AssessmentComponentCreate(name="Quiz", weight=50),
    ])
    with pytest.raises(InvalidBreakdownError):
        await svc.upsert_breakdown(
            user_id=w["instr_user"].id,
            section_id=w["section"].id, course_id=w["course"].id,
            payload=bad,
        )


async def test_breakdown_create_then_replace_bumps_version(
    async_session, grading_scenario,
):
    w = grading_scenario
    svc = InstructorGradingService(async_session)
    await svc.upsert_breakdown(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
        payload=_three_one_breakdown(),
    )
    resp = await svc.upsert_breakdown(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
        payload=AssessmentBreakdownCreate(components=[
            AssessmentComponentCreate(name="Final", weight=100),
        ]),
    )
    assert resp.version == 2
    assert len(resp.components) == 1


async def test_max_score_defaults_to_weight_when_omitted(
    async_session, grading_scenario,
):
    """
    Omitting ``max_score`` means the instructor grades on the same
    scale as the weight — the raw score becomes the weighted
    contribution directly, no scaling needed.
    """
    w = grading_scenario
    svc = InstructorGradingService(async_session)
    payload = AssessmentBreakdownCreate(components=[
        AssessmentComponentCreate(name="Quiz",  weight=10),  # no max_score
        AssessmentComponentCreate(name="Mid",   weight=30),  # no max_score
        AssessmentComponentCreate(name="Final", weight=60),  # no max_score
    ])
    resp = await svc.upsert_breakdown(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
        payload=payload,
    )
    by_name = {c.name: c for c in resp.components}
    assert by_name["Quiz"].max_score  == 10
    assert by_name["Mid"].max_score   == 30
    assert by_name["Final"].max_score == 60


async def test_explicit_max_score_overrides_default(
    async_session, grading_scenario,
):
    """An explicit max_score wins over the weight-defaulting."""
    w = grading_scenario
    svc = InstructorGradingService(async_session)
    payload = AssessmentBreakdownCreate(components=[
        AssessmentComponentCreate(name="Quiz",  weight=30, max_score=10),
        AssessmentComponentCreate(name="Final", weight=70, max_score=100),
    ])
    resp = await svc.upsert_breakdown(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
        payload=payload,
    )
    by_name = {c.name: c for c in resp.components}
    assert by_name["Quiz"].max_score  == 10   # explicit
    assert by_name["Final"].max_score == 100  # explicit


async def test_unauthorized_instructor_cannot_create_breakdown(
    async_session, grading_scenario,
):
    """Another instructor (no slot) is denied 403."""
    w = grading_scenario
    other_user = User(
        id=uuid.uuid4(), email="other-pr2@aau.edu.et",
        first_name="Other", last_name="Teacher",
        hashed_password="x", role=UserRole.INSTRUCTOR, is_active=True,
    )
    async_session.add(other_user)
    await async_session.flush()
    other_instr = Instructor(
        user_id=other_user.id, instructor_id="STAFF/OTH/15",
        department="Computer Science",
    )
    async_session.add(other_instr)
    await async_session.flush()

    svc = InstructorGradingService(async_session)
    with pytest.raises(UnauthorizedActorError):
        await svc.upsert_breakdown(
            user_id=other_user.id,
            section_id=w["section"].id, course_id=w["course"].id,
            payload=_three_one_breakdown(),
        )


# ── Batch get-or-create + idempotency ──────────────────────────


async def test_batch_get_or_create_returns_same_id_twice(
    async_session, grading_scenario,
):
    w = grading_scenario
    svc = InstructorGradingService(async_session)
    await svc.upsert_breakdown(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
        payload=_three_one_breakdown(),
    )
    b1 = await svc.get_or_create_batch(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
    )
    b2 = await svc.get_or_create_batch(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
    )
    assert b1.id == b2.id
    assert b1.status is GradeSubmissionStatus.DRAFT
    assert {r.student_number for r in b1.rows} == {
        "UGR/0001/15", "UGR/0002/15", "UGR/0003/15",
    }
    assert all(r.is_complete is False for r in b1.rows)


async def test_batch_create_without_breakdown_404(
    async_session, grading_scenario,
):
    w = grading_scenario
    svc = InstructorGradingService(async_session)
    with pytest.raises(EntityNotFoundError):
        await svc.get_or_create_batch(
            user_id=w["instr_user"].id,
            section_id=w["section"].id, course_id=w["course"].id,
        )


# ── Score upsert + lock-on-first-entry ──────────────────────────


async def test_first_score_save_locks_breakdown(
    async_session, grading_scenario,
):
    w = grading_scenario
    svc = InstructorGradingService(async_session)
    bd = await svc.upsert_breakdown(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
        payload=_three_one_breakdown(),
    )
    batch = await svc.get_or_create_batch(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
    )
    quiz_comp_id = next(c.id for c in bd.components if c.name == "Quiz")

    await svc.upsert_scores(
        user_id=w["instr_user"].id, batch_id=batch.id,
        payload=BulkScoreWrite(cells=[
            ScoreCellWrite(
                student_id=w["students"][0].id,
                component_id=quiz_comp_id, score=8,
            ),
        ]),
    )
    # Breakdown is now locked — re-upsert must fail.
    with pytest.raises(BreakdownLockedError):
        await svc.upsert_breakdown(
            user_id=w["instr_user"].id,
            section_id=w["section"].id, course_id=w["course"].id,
            payload=_three_one_breakdown(),
        )


async def test_null_only_save_does_not_lock(async_session, grading_scenario):
    """Saving a null-score cell shouldn't lock — it's the equivalent of clearing."""
    w = grading_scenario
    svc = InstructorGradingService(async_session)
    bd = await svc.upsert_breakdown(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
        payload=_three_one_breakdown(),
    )
    batch = await svc.get_or_create_batch(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
    )
    quiz_comp_id = next(c.id for c in bd.components if c.name == "Quiz")

    await svc.upsert_scores(
        user_id=w["instr_user"].id, batch_id=batch.id,
        payload=BulkScoreWrite(cells=[
            ScoreCellWrite(
                student_id=w["students"][0].id,
                component_id=quiz_comp_id, score=None,
            ),
        ]),
    )
    # Still editable.
    new_bd = await svc.upsert_breakdown(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
        payload=_three_one_breakdown(),
    )
    assert new_bd.version == 2


async def test_score_rejects_over_max(async_session, grading_scenario):
    w = grading_scenario
    svc = InstructorGradingService(async_session)
    bd = await svc.upsert_breakdown(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
        payload=_three_one_breakdown(),
    )
    batch = await svc.get_or_create_batch(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
    )
    quiz_id = next(c.id for c in bd.components if c.name == "Quiz")

    with pytest.raises(InvalidBreakdownError):
        await svc.upsert_scores(
            user_id=w["instr_user"].id, batch_id=batch.id,
            payload=BulkScoreWrite(cells=[
                ScoreCellWrite(
                    student_id=w["students"][0].id,
                    component_id=quiz_id, score=11,  # max is 10
                ),
            ]),
        )


async def test_score_rejects_off_roster_student(async_session, grading_scenario):
    w = grading_scenario
    svc = InstructorGradingService(async_session)
    bd = await svc.upsert_breakdown(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
        payload=_three_one_breakdown(),
    )
    batch = await svc.get_or_create_batch(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
    )
    quiz_id = next(c.id for c in bd.components if c.name == "Quiz")

    with pytest.raises(InvalidBreakdownError):
        await svc.upsert_scores(
            user_id=w["instr_user"].id, batch_id=batch.id,
            payload=BulkScoreWrite(cells=[
                ScoreCellWrite(
                    student_id=uuid.uuid4(),  # not on roster
                    component_id=quiz_id, score=5,
                ),
            ]),
        )


async def test_score_upsert_replaces_existing_value(
    async_session, grading_scenario,
):
    w = grading_scenario
    svc = InstructorGradingService(async_session)
    bd = await svc.upsert_breakdown(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
        payload=_three_one_breakdown(),
    )
    batch = await svc.get_or_create_batch(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
    )
    quiz_id = next(c.id for c in bd.components if c.name == "Quiz")

    cell = ScoreCellWrite(
        student_id=w["students"][0].id,
        component_id=quiz_id, score=5,
    )
    await svc.upsert_scores(
        user_id=w["instr_user"].id, batch_id=batch.id,
        payload=BulkScoreWrite(cells=[cell]),
    )
    # Second save overrides
    await svc.upsert_scores(
        user_id=w["instr_user"].id, batch_id=batch.id,
        payload=BulkScoreWrite(cells=[
            ScoreCellWrite(
                student_id=w["students"][0].id,
                component_id=quiz_id, score=9,
            ),
        ]),
    )
    rows = (await async_session.execute(
        select(StudentComponentScore).where(
            StudentComponentScore.batch_id == batch.id,
            StudentComponentScore.student_id == w["students"][0].id,
        )
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].score == 9


# ── Submit ─────────────────────────────────────────────────────


async def _enter_full_scores(
    svc: InstructorGradingService,
    *,
    user_id: uuid.UUID,
    batch_id: uuid.UUID,
    breakdown_components: list,
    students: list[Student],
    score_grid: dict[str, dict[str, float]],
) -> None:
    """
    Helper: enter a full grid of scores keyed by student_number then
    component name.
    """
    comp_by_name = {c.name: c for c in breakdown_components}
    cells: list[ScoreCellWrite] = []
    for s in students:
        for cname, score in score_grid[s.student_id].items():
            cells.append(ScoreCellWrite(
                student_id=s.id,
                component_id=comp_by_name[cname].id,
                score=score,
            ))
    await svc.upsert_scores(
        user_id=user_id, batch_id=batch_id,
        payload=BulkScoreWrite(cells=cells),
    )


async def test_submit_rejects_when_any_cell_missing(
    async_session, grading_scenario,
):
    w = grading_scenario
    svc = InstructorGradingService(async_session)
    bd = await svc.upsert_breakdown(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
        payload=_three_one_breakdown(),
    )
    batch = await svc.get_or_create_batch(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
    )
    # Enter only quizzes — leave Mid and Final empty.
    quiz_id = next(c.id for c in bd.components if c.name == "Quiz")
    await svc.upsert_scores(
        user_id=w["instr_user"].id, batch_id=batch.id,
        payload=BulkScoreWrite(cells=[
            ScoreCellWrite(student_id=s.id, component_id=quiz_id, score=8)
            for s in w["students"]
        ]),
    )
    with pytest.raises(IncompleteGradeSubmissionError) as ei:
        await svc.submit_batch(user_id=w["instr_user"].id, batch_id=batch.id)
    assert len(ei.value.missing) == 3  # all three students missing Mid + Final


async def test_submit_happy_path_writes_grades_and_approves(
    async_session, grading_scenario,
):
    """End-to-end: enter all cells, submit, verify Grade rows + verdict."""
    w = grading_scenario
    svc = InstructorGradingService(async_session)
    bd = await svc.upsert_breakdown(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
        payload=_three_one_breakdown(),
    )
    batch = await svc.get_or_create_batch(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
    )

    # Build comp lookup from the breakdown (which carries the IDs).
    # Each student gets perfect scores → 100% → letter A.
    quiz_id  = next(c.id for c in bd.components if c.name == "Quiz")
    mid_id   = next(c.id for c in bd.components if c.name == "Mid")
    final_id = next(c.id for c in bd.components if c.name == "Final")

    cells = []
    for s in w["students"]:
        cells.append(ScoreCellWrite(student_id=s.id, component_id=quiz_id,  score=10))
        cells.append(ScoreCellWrite(student_id=s.id, component_id=mid_id,   score=50))
        cells.append(ScoreCellWrite(student_id=s.id, component_id=final_id, score=100))
    await svc.upsert_scores(
        user_id=w["instr_user"].id, batch_id=batch.id,
        payload=BulkScoreWrite(cells=cells),
    )

    result = await svc.submit_batch(
        user_id=w["instr_user"].id, batch_id=batch.id,
    )
    assert result.agent_verdict == "APPROVE"
    assert result.status is GradeSubmissionStatus.SUBMITTED
    assert len(result.grades) == 3
    assert all(g.letter_grade is GradeLetter.A for g in result.grades)
    assert all(g.numeric_score == 100.0 for g in result.grades)

    # Grade rows landed in the Track A table at SUBMITTED status.
    grades = (await async_session.execute(
        select(Grade).where(
            Grade.course_id == w["course"].id,
            Grade.term_id == w["term"].id,
        )
    )).scalars().all()
    assert len(grades) == 3
    assert all(g.status is GradeSubmissionStatus.SUBMITTED for g in grades)
    assert all(g.letter_grade is GradeLetter.A for g in grades)
    # grade_points = credit_hours (3) × 4.0 (A) = 12.0
    assert all(g.grade_points == 12.0 for g in grades)
    assert all(g.entered_by_id == w["instr_user"].id for g in grades)


async def test_submit_mixed_outcomes_compute_correctly(
    async_session, grading_scenario,
):
    """
    Mixed grades — confirm the weighted_pct math.

      Quiz weight 30, max 10. Mid weight 30, max 50. Final weight 40, max 100.

    Student 1 (perfect):     10/10  + 50/50  + 100/100 → 30+30+40 = 100 → A
    Student 2 (middling):    7/10   + 35/50  + 70/100  → 21+21+28 = 70  → B-
    Student 3 (failing):     2/10   + 10/50  + 30/100  →  6+ 6+12 = 24  → F
    """
    w = grading_scenario
    svc = InstructorGradingService(async_session)
    bd = await svc.upsert_breakdown(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
        payload=_three_one_breakdown(),
    )
    batch = await svc.get_or_create_batch(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
    )
    quiz_id  = next(c.id for c in bd.components if c.name == "Quiz")
    mid_id   = next(c.id for c in bd.components if c.name == "Mid")
    final_id = next(c.id for c in bd.components if c.name == "Final")

    grid = [
        (w["students"][0], 10, 50, 100),
        (w["students"][1],  7, 35,  70),
        (w["students"][2],  2, 10,  30),
    ]
    cells = []
    for s, q, m, f in grid:
        cells.append(ScoreCellWrite(student_id=s.id, component_id=quiz_id,  score=q))
        cells.append(ScoreCellWrite(student_id=s.id, component_id=mid_id,   score=m))
        cells.append(ScoreCellWrite(student_id=s.id, component_id=final_id, score=f))
    await svc.upsert_scores(
        user_id=w["instr_user"].id, batch_id=batch.id,
        payload=BulkScoreWrite(cells=cells),
    )

    result = await svc.submit_batch(
        user_id=w["instr_user"].id, batch_id=batch.id,
    )
    by_sn = {g.student_number: g for g in result.grades}
    assert by_sn["UGR/0001/15"].numeric_score == 100.0
    assert by_sn["UGR/0001/15"].letter_grade is GradeLetter.A
    assert by_sn["UGR/0002/15"].numeric_score == 70.0
    assert by_sn["UGR/0002/15"].letter_grade is GradeLetter.B_MINUS
    assert by_sn["UGR/0003/15"].numeric_score == 24.0
    assert by_sn["UGR/0003/15"].letter_grade is GradeLetter.F


async def test_delete_scores_clears_cells_and_unlocks_breakdown(
    async_session, grading_scenario,
):
    """
    The unlock path: enter some scores → breakdown locks → DELETE
    /scores wipes the cells → breakdown is editable again → a fresh
    POST /breakdown succeeds with a brand-new shape.
    """
    w = grading_scenario
    svc = InstructorGradingService(async_session)
    bd = await svc.upsert_breakdown(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
        payload=_three_one_breakdown(),
    )
    batch = await svc.get_or_create_batch(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
    )
    quiz_id = next(c.id for c in bd.components if c.name == "Quiz")
    await svc.upsert_scores(
        user_id=w["instr_user"].id, batch_id=batch.id,
        payload=BulkScoreWrite(cells=[
            ScoreCellWrite(
                student_id=w["students"][0].id,
                component_id=quiz_id, score=8,
            ),
        ]),
    )
    # Lock asserted by the existing test; here we just confirm the
    # delete path opens it back up.
    cleared = await svc.delete_all_scores(
        user_id=w["instr_user"].id, batch_id=batch.id,
    )
    assert cleared.id == batch.id
    assert cleared.status is GradeSubmissionStatus.DRAFT
    assert all(
        cell.score is None for row in cleared.rows for cell in row.scores
    )

    # No StudentComponentScore rows survive.
    surviving = (await async_session.execute(
        select(StudentComponentScore).where(
            StudentComponentScore.batch_id == batch.id,
        )
    )).scalars().all()
    assert surviving == []

    # Breakdown is unlocked and a new POST is accepted.
    new_bd = await svc.upsert_breakdown(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
        payload=AssessmentBreakdownCreate(components=[
            AssessmentComponentCreate(name="Project", weight=50),
            AssessmentComponentCreate(name="Final",   weight=50),
        ]),
    )
    assert new_bd.version == 2
    assert {c.name for c in new_bd.components} == {"Project", "Final"}
    assert new_bd.locked_at is None


async def test_delete_scores_rejected_after_submit(
    async_session, grading_scenario,
):
    """Once SUBMITTED, the wipe path is off-limits — go through DH review."""
    w = grading_scenario
    svc = InstructorGradingService(async_session)
    bd = await svc.upsert_breakdown(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
        payload=_three_one_breakdown(),
    )
    batch = await svc.get_or_create_batch(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
    )
    comps = {c.name: c.id for c in bd.components}
    cells = []
    for s in w["students"]:
        cells.append(ScoreCellWrite(student_id=s.id, component_id=comps["Quiz"],  score=10))
        cells.append(ScoreCellWrite(student_id=s.id, component_id=comps["Mid"],   score=50))
        cells.append(ScoreCellWrite(student_id=s.id, component_id=comps["Final"], score=100))
    await svc.upsert_scores(
        user_id=w["instr_user"].id, batch_id=batch.id,
        payload=BulkScoreWrite(cells=cells),
    )
    await svc.submit_batch(user_id=w["instr_user"].id, batch_id=batch.id)

    with pytest.raises(GradeBatchNotEditableError):
        await svc.delete_all_scores(
            user_id=w["instr_user"].id, batch_id=batch.id,
        )


async def test_delete_scores_denied_to_other_instructor(
    async_session, grading_scenario,
):
    w = grading_scenario
    other_user = User(
        id=uuid.uuid4(), email="otherdel-pr2@aau.edu.et",
        first_name="Other", last_name="Teacher",
        hashed_password="x", role=UserRole.INSTRUCTOR, is_active=True,
    )
    async_session.add(other_user)
    await async_session.flush()
    async_session.add(Instructor(
        user_id=other_user.id, instructor_id="STAFF/DEL/15",
        department="Computer Science",
    ))
    await async_session.flush()

    svc = InstructorGradingService(async_session)
    await svc.upsert_breakdown(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
        payload=_three_one_breakdown(),
    )
    batch = await svc.get_or_create_batch(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
    )
    with pytest.raises(UnauthorizedActorError):
        await svc.delete_all_scores(
            user_id=other_user.id, batch_id=batch.id,
        )


async def test_resubmit_after_submitted_is_blocked(
    async_session, grading_scenario,
):
    """Once SUBMITTED, the batch refuses score edits and re-submit."""
    w = grading_scenario
    svc = InstructorGradingService(async_session)
    bd = await svc.upsert_breakdown(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
        payload=_three_one_breakdown(),
    )
    batch = await svc.get_or_create_batch(
        user_id=w["instr_user"].id,
        section_id=w["section"].id, course_id=w["course"].id,
    )
    comps = {c.name: c.id for c in bd.components}
    cells = []
    for s in w["students"]:
        cells.append(ScoreCellWrite(student_id=s.id, component_id=comps["Quiz"],  score=10))
        cells.append(ScoreCellWrite(student_id=s.id, component_id=comps["Mid"],   score=50))
        cells.append(ScoreCellWrite(student_id=s.id, component_id=comps["Final"], score=100))
    await svc.upsert_scores(
        user_id=w["instr_user"].id, batch_id=batch.id,
        payload=BulkScoreWrite(cells=cells),
    )
    await svc.submit_batch(user_id=w["instr_user"].id, batch_id=batch.id)

    with pytest.raises(GradeBatchNotEditableError):
        await svc.submit_batch(
            user_id=w["instr_user"].id, batch_id=batch.id,
        )
    with pytest.raises(GradeBatchNotEditableError):
        await svc.upsert_scores(
            user_id=w["instr_user"].id, batch_id=batch.id,
            payload=BulkScoreWrite(cells=[
                ScoreCellWrite(
                    student_id=w["students"][0].id,
                    component_id=comps["Quiz"], score=5,
                ),
            ]),
        )
