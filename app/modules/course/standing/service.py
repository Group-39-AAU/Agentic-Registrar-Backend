"""
Track C (Academic Standing) — service layer.

PR C1 scope: read-only DH-or-officer browse flow for the three-
dropdown UI (term → department → section → students). The roster
endpoint composes the per-student preview by joining authorised
``Grade`` rows from Track A with the section's cohort registrations
and any add/drop deltas.

Auth model: every read accepts the calling user when their role is
REGISTRAR_OFFICER, DEPARTMENT_HEAD (as identified by the
:class:`CourseManagementOfficer` row), or ADMIN. Writes — which
land in PR C2 — will further narrow authorise/override to
``OfficerRole.DEPARTMENT_HEAD``.

Math here is the *live* view from authorised grades, not the
persisted :class:`AcademicStanding` row. PR C2's agent will
upsert standing rows; this PR shows what an officer can see
*before* that compute step runs.
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import and_, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import User
from app.modules.course.exceptions import (
    EntityNotFoundError, UnauthorizedActorError,
)
from app.modules.course.grade_points import counts_toward_cgpa, points_for
from app.modules.course.models import (
    AcademicTerm, Course, CourseManagementOfficer, Grade, Registration,
    RegistrationCourse, Section, Student, StudentScheduleAddition,
)
from app.modules.course.standing.models import AcademicStanding
from app.modules.course.standing.schemas import (
    ExistingStandingSummary, StandingDepartmentResponse, StandingRosterResponse,
    StandingSectionResponse, StandingTermResponse, StudentStandingPreview,
    StudentTermGrade,
)
from app.shared.enums import (
    EnrollmentStatus, GradeLetter, GradeSubmissionStatus, OfficerRole,
    RegistrationStatus, UserRole,
)


# Registration states the standing view treats as "the student is
# actually enrolled in this section". Drafts and cancelled
# registrations are excluded — they don't appear on the roster.
_ATTENDING_STATES: frozenset[RegistrationStatus] = frozenset({
    RegistrationStatus.REGISTERED,
    RegistrationStatus.ADD_DROP_WINDOW,
})


# Letters that indicate the term should be held for officer review
# under Article 90.7 (no numeric verdict computed). NG and I are the
# two that imply unresolved coursework; W / DO / P are administrative
# marks that simply don't count toward GPA without flagging the term.
_HOLD_FOR_REVIEW_LETTERS: frozenset[GradeLetter] = frozenset({
    GradeLetter.I, GradeLetter.NG,
})


class StandingService:
    """
    Read surface for the DH standing-roster browse flow.

    The service is intentionally stateless; every method opens its
    own queries against the injected session. Future PR C2 will
    add ``compute_term_standing`` / ``authorise`` / ``override``
    methods that write to ``AcademicStanding``.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Auth gate ───────────────────────────────────────────────

    async def _resolve_officer_or_403(self, user_id: uuid.UUID) -> User:
        """
        Reads are open to REGISTRAR_OFFICER, DEPARTMENT_HEAD, and
        ADMIN. The role discriminator is checked against the User row
        for the lightweight cases (admin) and against
        :class:`CourseManagementOfficer` for the officer cases — so
        someone whose JWT carries REGISTRAR_OFFICER but who has no
        underlying officer row gets 403, mirroring the pattern
        established by Track B's DH service.
        """
        user = (await self.db.execute(
            select(User).where(User.id == user_id)
        )).scalar_one_or_none()
        if user is None:
            raise UnauthorizedActorError(
                "Calling user not found."
            )
        if user.role == UserRole.ADMIN:
            return user
        if user.role != UserRole.REGISTRAR_OFFICER:
            raise UnauthorizedActorError(
                "Standing browse is open to registrar officers, "
                "department heads, and admins only."
            )
        # JWT carries REGISTRAR_OFFICER — confirm an officer row exists.
        officer = (await self.db.execute(
            select(CourseManagementOfficer).where(
                CourseManagementOfficer.user_id == user_id,
                CourseManagementOfficer.is_deleted == False,  # noqa: E712
            )
        )).scalar_one_or_none()
        if officer is None:
            raise UnauthorizedActorError(
                "Calling user has no Course Management officer profile."
            )
        # Role within the officer profile is permissive for reads;
        # both REGISTRAR_OFFICER and DEPARTMENT_HEAD may browse.
        if officer.role not in {
            OfficerRole.REGISTRAR_OFFICER, OfficerRole.DEPARTMENT_HEAD,
        }:
            raise UnauthorizedActorError(
                "Officer role does not permit standing browse."
            )
        return user

    # ── Dropdown 1: terms ───────────────────────────────────────

    async def list_terms(
        self, *, user_id: uuid.UUID,
    ) -> list[StandingTermResponse]:
        """
        Every (non-deleted) term, newest-first by start_date. Each
        entry carries a ``has_authorised_grades`` flag so the UI can
        visually disable terms without grades to evaluate.
        """
        await self._resolve_officer_or_403(user_id)

        terms = (await self.db.execute(
            select(AcademicTerm)
            .where(AcademicTerm.is_deleted == False)  # noqa: E712
            .order_by(AcademicTerm.start_date.desc())
        )).scalars().all()

        # One bulk query to find terms with authorised grades.
        terms_with_grades = set(
            (await self.db.execute(
                select(distinct(Grade.term_id))
                .where(
                    Grade.status == GradeSubmissionStatus.AUTHORISED,
                    Grade.is_deleted == False,  # noqa: E712
                )
            )).scalars().all()
        )

        return [
            StandingTermResponse(
                id=t.id,
                term_name=t.term_name,
                phase=t.phase.value,
                start_date=t.start_date,
                end_date=t.end_date,
                is_open=t.is_open,
                has_authorised_grades=(t.id in terms_with_grades),
            )
            for t in terms
        ]

    # ── Dropdown 2: departments in a term ───────────────────────

    async def list_departments(
        self, *, user_id: uuid.UUID, term_id: uuid.UUID,
    ) -> list[StandingDepartmentResponse]:
        """
        Every department that has at least one Section in the term,
        with section + student counts. Sorted alphabetically so the
        dropdown is predictable.
        """
        await self._resolve_officer_or_403(user_id)
        await self._resolve_term_or_404(term_id)

        # Section count per department.
        section_rows = (await self.db.execute(
            select(Section.department, func.count(Section.id))
            .where(
                Section.term_id == term_id,
                Section.is_deleted == False,  # noqa: E712
            )
            .group_by(Section.department)
        )).all()
        sections_per_dept = {row[0]: row[1] for row in section_rows}

        # Student count per department (across all sections in term).
        student_rows = (await self.db.execute(
            select(Section.department, func.count(Registration.id))
            .join(Section, Section.id == Registration.section_id)
            .where(
                Registration.term_id == term_id,
                Registration.status.in_(_ATTENDING_STATES),
                Registration.is_deleted == False,  # noqa: E712
                Section.is_deleted == False,  # noqa: E712
            )
            .group_by(Section.department)
        )).all()
        students_per_dept = {row[0]: row[1] for row in student_rows}

        out = [
            StandingDepartmentResponse(
                department=dept,
                section_count=count,
                student_count=students_per_dept.get(dept, 0),
            )
            for dept, count in sections_per_dept.items()
        ]
        out.sort(key=lambda r: r.department)
        return out

    # ── Dropdown 3: sections in a (term, department) ────────────

    async def list_sections(
        self, *, user_id: uuid.UUID,
        term_id: uuid.UUID, department: str,
    ) -> list[StandingSectionResponse]:
        """
        Sections sorted by (semester, section_code) so the dropdown
        reads year-by-year top-down.
        """
        await self._resolve_officer_or_403(user_id)
        await self._resolve_term_or_404(term_id)

        sections = (await self.db.execute(
            select(Section)
            .where(
                Section.term_id == term_id,
                Section.department == department,
                Section.is_deleted == False,  # noqa: E712
            )
            .order_by(Section.semester.asc(), Section.section_code.asc())
        )).scalars().all()

        return [StandingSectionResponse.model_validate(s) for s in sections]

    # ── Roster detail ───────────────────────────────────────────

    async def get_section_roster(
        self, *, user_id: uuid.UUID,
        term_id: uuid.UUID, section_id: uuid.UUID,
    ) -> StandingRosterResponse:
        """
        Every cohort member of (term, section) with their authorised
        term grades and live-computed SGPA / CGPA / status context.
        """
        await self._resolve_officer_or_403(user_id)
        term = await self._resolve_term_or_404(term_id)
        section = await self._resolve_section_or_404(section_id)

        # Cohort members: students whose Registration.section_id
        # points at this section in this term, in an attending state.
        cohort_rows = (await self.db.execute(
            select(Registration, Student)
            .join(Student, Student.id == Registration.student_id)
            .where(
                Registration.term_id == term_id,
                Registration.section_id == section_id,
                Registration.status.in_(_ATTENDING_STATES),
                Registration.is_deleted == False,  # noqa: E712
                Student.is_deleted == False,  # noqa: E712
                Student.enrollment_status == EnrollmentStatus.ACTIVE,
            )
            .order_by(Student.student_id.asc())
        )).all()

        if not cohort_rows:
            return StandingRosterResponse(
                term_id=term.id,
                term_name=term.term_name,
                section_id=section.id,
                section_code=section.section_code,
                department=section.department,
                semester=section.semester,
                students=[],
            )

        student_ids = [s.id for _r, s in cohort_rows]
        registration_by_student = {r.student_id: r for r, _s in cohort_rows}

        # Bulk-load authorised grades for the cohort across all terms
        # in one query, then partition by (student, term).
        grades = (await self.db.execute(
            select(Grade)
            .where(
                Grade.student_id.in_(student_ids),
                Grade.status == GradeSubmissionStatus.AUTHORISED,
                Grade.is_deleted == False,  # noqa: E712
            )
        )).scalars().all()

        # Hydrate course metadata in one batch query.
        course_ids = list({g.course_id for g in grades})
        courses_by_id: dict[uuid.UUID, Course] = {}
        if course_ids:
            course_rows = (await self.db.execute(
                select(Course).where(Course.id.in_(course_ids))
            )).scalars().all()
            courses_by_id = {c.id: c for c in course_rows}

        # RegistrationCourse.is_dropped lookup per (registration, course).
        reg_ids = [r.id for r, _s in cohort_rows]
        rc_rows: list[RegistrationCourse] = []
        if reg_ids:
            rc_rows = (await self.db.execute(
                select(RegistrationCourse)
                .where(RegistrationCourse.registration_id.in_(reg_ids))
            )).scalars().all()
        dropped_by_reg: dict[uuid.UUID, set[uuid.UUID]] = {}
        original_by_reg: dict[uuid.UUID, set[uuid.UUID]] = {}
        for rc in rc_rows:
            original_by_reg.setdefault(rc.registration_id, set()).add(rc.course_id)
            if rc.is_dropped:
                dropped_by_reg.setdefault(rc.registration_id, set()).add(rc.course_id)

        # Added-via-drop courses per (registration).
        added_by_reg: dict[uuid.UUID, set[uuid.UUID]] = {}
        if reg_ids:
            add_rows = (await self.db.execute(
                select(
                    StudentScheduleAddition.registration_id,
                    StudentScheduleAddition.course_id,
                )
                .where(StudentScheduleAddition.registration_id.in_(reg_ids))
                .distinct()
            )).all()
            for r_id, c_id in add_rows:
                added_by_reg.setdefault(r_id, set()).add(c_id)

        # Existing standing rows (if PR C2 already computed for these
        # students). One query for the whole cohort.
        standings_by_student: dict[uuid.UUID, AcademicStanding] = {}
        existing_rows = (await self.db.execute(
            select(AcademicStanding)
            .where(
                AcademicStanding.student_id.in_(student_ids),
                AcademicStanding.term_id == term_id,
                AcademicStanding.is_deleted == False,  # noqa: E712
            )
        )).scalars().all()
        standings_by_student = {s.student_id: s for s in existing_rows}

        # Compose one preview per student.
        students: list[StudentStandingPreview] = []
        for _reg, stu in cohort_rows:
            students.append(self._compose_student_preview(
                student=stu,
                registration_id=registration_by_student[stu.id].id,
                term_id=term_id,
                all_grades=[g for g in grades if g.student_id == stu.id],
                courses_by_id=courses_by_id,
                original_courses=original_by_reg.get(
                    registration_by_student[stu.id].id, set(),
                ),
                dropped_courses=dropped_by_reg.get(
                    registration_by_student[stu.id].id, set(),
                ),
                added_courses=added_by_reg.get(
                    registration_by_student[stu.id].id, set(),
                ),
                existing_standing=standings_by_student.get(stu.id),
            ))

        return StandingRosterResponse(
            term_id=term.id,
            term_name=term.term_name,
            section_id=section.id,
            section_code=section.section_code,
            department=section.department,
            semester=section.semester,
            students=students,
        )

    # ── Helpers ─────────────────────────────────────────────────

    def _compose_student_preview(
        self,
        *,
        student: Student,
        registration_id: uuid.UUID,
        term_id: uuid.UUID,
        all_grades: list[Grade],
        courses_by_id: dict[uuid.UUID, Course],
        original_courses: set[uuid.UUID],
        dropped_courses: set[uuid.UUID],
        added_courses: set[uuid.UUID],
        existing_standing: Optional[AcademicStanding],
    ) -> StudentStandingPreview:
        """
        Compose one student's roster row from pre-loaded data. No
        DB access from here — every query happens upstream so
        per-student composition is in-memory.
        """
        _ = registration_id  # reserved for richer add/drop UX later

        # Partition the student's grades by term.
        term_grades = [g for g in all_grades if g.term_id == term_id]
        prior_grades = [g for g in all_grades if g.term_id != term_id]

        # Build the per-course rows for the target term.
        term_rows: list[StudentTermGrade] = []
        for g in term_grades:
            course = courses_by_id.get(g.course_id)
            if course is None:
                continue
            term_rows.append(StudentTermGrade(
                course_id=course.id,
                course_code=course.code,
                course_title=course.title,
                credit_hours=g.credit_hours,
                letter_grade=g.letter_grade,
                numeric_score=g.numeric_score,
                grade_points=g.grade_points,
                is_dropped=(course.id in dropped_courses),
                is_added_via_drop=(course.id in added_courses),
            ))

        # Surface dropped courses that have no Grade row yet (the
        # student dropped before the instructor entered grades) — the
        # officer needs to see "they had this on the registration".
        graded_course_ids = {r.course_id for r in term_rows}
        for course_id in dropped_courses - graded_course_ids:
            course = courses_by_id.get(course_id)
            if course is None:
                # Hydrate from the course catalog only if we don't
                # already have it from the grades-load path.
                continue
            term_rows.append(StudentTermGrade(
                course_id=course.id,
                course_code=course.code,
                course_title=course.title,
                credit_hours=course.credit_hours,
                letter_grade=None,
                numeric_score=None,
                grade_points=None,
                is_dropped=True,
                is_added_via_drop=False,
            ))

        term_rows.sort(key=lambda r: r.course_code)

        # ── SGPA math ── credit-weighted over grades counting toward
        # CGPA (excludes I, NG, W, DO, P per Senate Art 90.7).
        sgpa_grades = [g for g in term_grades if counts_toward_cgpa(g.letter_grade)]
        term_credit = sum(g.credit_hours for g in sgpa_grades)
        sgpa_points = sum(
            (g.grade_points if g.grade_points is not None
             else (points_for(g.letter_grade) or 0.0) * g.credit_hours)
            for g in sgpa_grades
        )
        sgpa = round(sgpa_points / term_credit, 4) if term_credit > 0 else None

        # ── CGPA math ── across every authorised term-counting grade.
        cgpa_grades = [
            g for g in all_grades if counts_toward_cgpa(g.letter_grade)
        ]
        cumulative_credit = sum(g.credit_hours for g in cgpa_grades)
        cgpa_points = sum(
            (g.grade_points if g.grade_points is not None
             else (points_for(g.letter_grade) or 0.0) * g.credit_hours)
            for g in cgpa_grades
        )
        cgpa = (
            round(cgpa_points / cumulative_credit, 4)
            if cumulative_credit > 0 else None
        )

        # ── Article-91 evaluation context ──
        f_count = sum(
            1 for g in term_grades if g.letter_grade == GradeLetter.F
        )
        f_credit = sum(
            g.credit_hours for g in term_grades
            if g.letter_grade == GradeLetter.F
        )

        # is_first_semester / is_first_year derive from how many
        # distinct *prior* terms the student has authorised grades in.
        prior_term_count = len({g.term_id for g in prior_grades})
        is_first_semester = prior_term_count == 0
        is_first_year = prior_term_count <= 1

        has_incomplete = any(
            g.letter_grade in _HOLD_FOR_REVIEW_LETTERS for g in term_grades
        )

        existing_summary = None
        if existing_standing is not None:
            existing_summary = ExistingStandingSummary.model_validate(
                existing_standing
            )

        return StudentStandingPreview(
            student_id=student.id,
            student_number=student.student_id,
            full_name=student.full_name,
            current_semester=student.current_semester,
            department=student.department,
            grades_this_term=term_rows,
            sgpa=sgpa,
            cgpa=cgpa,
            term_credit_hours=term_credit,
            cumulative_credit_hours=cumulative_credit,
            f_count_term=f_count,
            f_credit_total_term=f_credit,
            is_first_semester=is_first_semester,
            is_first_year=is_first_year,
            has_incomplete_marks=has_incomplete,
            existing_standing=existing_summary,
        )

    async def _resolve_term_or_404(
        self, term_id: uuid.UUID,
    ) -> AcademicTerm:
        term = (await self.db.execute(
            select(AcademicTerm).where(
                AcademicTerm.id == term_id,
                AcademicTerm.is_deleted == False,  # noqa: E712
            )
        )).scalar_one_or_none()
        if term is None:
            raise EntityNotFoundError("AcademicTerm", str(term_id))
        return term

    async def _resolve_section_or_404(
        self, section_id: uuid.UUID,
    ) -> Section:
        section = (await self.db.execute(
            select(Section).where(
                Section.id == section_id,
                Section.is_deleted == False,  # noqa: E712
            )
        )).scalar_one_or_none()
        if section is None:
            raise EntityNotFoundError("Section", str(section_id))
        return section
