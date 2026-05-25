"""
Track C (Academic Standing) — service layer.

PR C1 scope: read-only DH-or-officer browse flow.
PR C2 scope (this commit): compute / authorise / override writes,
the officer queue, and the per-standing GET endpoint.

Auth model:
  * Reads (browse + queue + get) accept REGISTRAR_OFFICER (with
    backing CourseManagementOfficer row) ∨ DEPARTMENT_HEAD ∨ ADMIN.
  * Writes (compute / authorise / override) accept only
    DEPARTMENT_HEAD (or ADMIN) — same gate as Track B's grading DH
    workflow.

The agent does the deterministic rule evaluation; the service
orchestrates DB loads, upserts the AcademicStanding row, writes
the history audit row, and fires a best-effort student email on
authorise / override.
"""
from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import and_, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import User
from app.modules.course.exceptions import (
    EntityNotFoundError, InvalidAdjustmentRequestError,
    InvalidStateTransitionError, StudentProfileRequiredError,
    UnauthorizedActorError,
)
from app.modules.course.grade_points import counts_toward_cgpa, points_for
from app.modules.course.models import (
    AcademicTerm, Course, CourseManagementOfficer, Grade, Registration,
    RegistrationCourse, Section, Student, StudentScheduleAddition,
)
from app.modules.course.standing.agents import (
    AcademicStandingAgent, StandingProposal,
)
from app.modules.course.standing.models import (
    AcademicStanding, AcademicStandingHistory,
)
from app.modules.course.standing.rules import (
    StandingComputeInput, TermGradeRow,
)
from app.modules.course.standing.schemas import (
    AcademicStandingResponse, BatchAuthoriseOutcome, BatchAuthoriseResponse,
    ExistingStandingSummary, PendingGradesRow, StandingComputeRow,
    StandingDepartmentResponse, StandingQueueEntry, StandingRosterResponse,
    StandingSectionResponse, StandingTermResponse, StudentStandingPreview,
    StudentStandingResponse, StudentStandingTranscriptResponse, StudentTermGrade,
)
from app.shared.email.schemas import EmailMessage
from app.shared.email.service import EmailService
from app.shared.enums import (
    AcademicStatusType, EnrollmentStatus, GradeLetter, GradeSubmissionStatus,
    OfficerRole, RegistrationStatus, UserRole,
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
    DH-and-officer surface for the academic-standing workflow.

    The service is intentionally stateless w.r.t. the agent; the
    agent is lazily constructed on first compute call. PR C2 add
    ``compute_term_standing`` / ``authorise`` / ``override`` write
    methods on top of the PR C1 browse reads.
    """

    def __init__(
        self,
        db: AsyncSession,
        *,
        agent: Optional[AcademicStandingAgent] = None,
        email_service: Optional[EmailService] = None,
    ) -> None:
        self.db = db
        self._agent = agent
        self._email_service = email_service

    def _resolve_agent(self) -> AcademicStandingAgent:
        if self._agent is None:
            self._agent = AcademicStandingAgent()
        return self._agent

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

    async def _caller_dh_department(
        self, user_id: uuid.UUID,
    ) -> Optional[str]:
        """
        For a DEPARTMENT_HEAD caller, return the department recorded
        on their ``CourseManagementOfficer`` row. Returns ``None`` for
        ADMIN, REGISTRAR_OFFICER, or any caller without a DH profile —
        used to auto-scope read endpoints so a DH only ever sees their
        own department's standings, mirroring the section-allocation
        and scheduling pattern.
        """
        user = (await self.db.execute(
            select(User).where(User.id == user_id)
        )).scalar_one_or_none()
        if user is None or user.role == UserRole.ADMIN:
            return None
        if user.role != UserRole.REGISTRAR_OFFICER:
            return None
        officer = (await self.db.execute(
            select(CourseManagementOfficer).where(
                CourseManagementOfficer.user_id == user_id,
                CourseManagementOfficer.is_deleted == False,  # noqa: E712
            )
        )).scalar_one_or_none()
        if officer is None or officer.role != OfficerRole.DEPARTMENT_HEAD:
            return None
        return officer.department

    async def _resolve_dh_or_403(self, user_id: uuid.UUID) -> User:
        """
        Write gate — only DEPARTMENT_HEAD (via CourseManagementOfficer
        row) or ADMIN. Mirrors Track B's DH-grading auth pattern so
        the role contract is consistent across modules.
        """
        user = (await self.db.execute(
            select(User).where(User.id == user_id)
        )).scalar_one_or_none()
        if user is None:
            raise UnauthorizedActorError("Calling user not found.")
        if user.role == UserRole.ADMIN:
            return user
        if user.role != UserRole.REGISTRAR_OFFICER:
            raise UnauthorizedActorError(
                "Only a Department Head (or admin) may write to "
                "academic standing."
            )
        officer = (await self.db.execute(
            select(CourseManagementOfficer).where(
                CourseManagementOfficer.user_id == user_id,
                CourseManagementOfficer.is_deleted == False,  # noqa: E712
            )
        )).scalar_one_or_none()
        if officer is None or officer.role != OfficerRole.DEPARTMENT_HEAD:
            raise UnauthorizedActorError(
                "Only a Department Head (or admin) may write to "
                "academic standing."
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
        caller_dept = await self._caller_dh_department(user_id)

        # Section count per department.
        section_stmt = (
            select(Section.department, func.count(Section.id))
            .where(
                Section.term_id == term_id,
                Section.is_deleted == False,  # noqa: E712
            )
            .group_by(Section.department)
        )
        if caller_dept is not None:
            section_stmt = section_stmt.where(Section.department == caller_dept)
        section_rows = (await self.db.execute(section_stmt)).all()
        sections_per_dept = {row[0]: row[1] for row in section_rows}

        # Student count per department (across all sections in term).
        student_stmt = (
            select(Section.department, func.count(Registration.id))
            .join(Section, Section.id == Registration.section_id)
            .where(
                Registration.term_id == term_id,
                Registration.status.in_(_ATTENDING_STATES),
                Registration.is_deleted == False,  # noqa: E712
                Section.is_deleted == False,  # noqa: E712
            )
            .group_by(Section.department)
        )
        if caller_dept is not None:
            student_stmt = student_stmt.where(Section.department == caller_dept)
        student_rows = (await self.db.execute(student_stmt)).all()
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
        caller_dept = await self._caller_dh_department(user_id)
        # A DH never sees sections outside their own department.
        if caller_dept is not None and caller_dept != department:
            return []

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

        # Hydrate course metadata for every course we might display:
        # graded ones (from the Grade ledger) + registered/added ones
        # (from RegistrationCourse + StudentScheduleAddition) so the
        # ungraded-course rows can render the catalog code/title.
        all_course_ids: set[uuid.UUID] = {g.course_id for g in grades}
        for cs in original_by_reg.values():
            all_course_ids.update(cs)
        for cs in added_by_reg.values():
            all_course_ids.update(cs)
        courses_by_id: dict[uuid.UUID, Course] = {}
        if all_course_ids:
            course_rows = (await self.db.execute(
                select(Course).where(Course.id.in_(all_course_ids))
            )).scalars().all()
            courses_by_id = {c.id: c for c in course_rows}

        # Existing standing rows for the target term (if PR C2 already
        # computed). One query for the whole cohort.
        existing_rows = (await self.db.execute(
            select(AcademicStanding)
            .where(
                AcademicStanding.student_id.in_(student_ids),
                AcademicStanding.term_id == term_id,
                AcademicStanding.is_deleted == False,  # noqa: E712
            )
        )).scalars().all()
        standings_by_student: dict[uuid.UUID, AcademicStanding] = {
            s.student_id: s for s in existing_rows
        }

        # Prior standings (any term before the target) — used to
        # surface the "incoming CGPA from last term" snapshot so the
        # UI has a CGPA carry-over even before the current term is
        # computed. Picks the most recent prior row by the term's
        # start_date so terms are ordered chronologically, not by
        # whichever DH happened to compute first.
        prior_rows = (await self.db.execute(
            select(AcademicStanding, AcademicTerm)
            .join(AcademicTerm, AcademicTerm.id == AcademicStanding.term_id)
            .where(
                AcademicStanding.student_id.in_(student_ids),
                AcademicStanding.term_id != term_id,
                AcademicTerm.start_date < term.start_date,
                AcademicStanding.is_deleted == False,  # noqa: E712
            )
            .order_by(AcademicTerm.start_date.asc())
        )).all()
        prior_by_student: dict[
            uuid.UUID, tuple[AcademicStanding, AcademicTerm],
        ] = {}
        for standing, t in prior_rows:
            # Last write wins → because ordered ASC, the final
            # write is the most recent prior term per student.
            prior_by_student[standing.student_id] = (standing, t)

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
                prior_standing=prior_by_student.get(stu.id),
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
        prior_standing: Optional[tuple[AcademicStanding, AcademicTerm]],
    ) -> StudentStandingPreview:
        """
        Compose one student's roster row from pre-loaded data. No
        DB access from here — every query happens upstream so
        per-student composition is in-memory.

        SGPA / CGPA come from the AcademicStanding snapshot for this
        term (or ``None`` until compute has run). Prior-term carry-
        over CGPA comes from the most recent prior snapshot. The
        roster never re-derives GPA from the Grade ledger — the
        snapshot is the authoritative answer per the standing-
        workflow design.
        """
        _ = registration_id  # reserved for richer add/drop UX later

        term_grades = [g for g in all_grades if g.term_id == term_id]
        prior_grades = [g for g in all_grades if g.term_id != term_id]

        # ── Build the expected-vs-graded picture of the term ──
        # Expected = registered (non-dropped) ∪ added-via-drop. Drops
        # are surfaced separately for audit context but don't count
        # toward "expected" because they're no longer the student's
        # responsibility.
        expected_courses = (original_courses - dropped_courses) | added_courses
        graded_by_course: dict[uuid.UUID, Grade] = {
            g.course_id: g for g in term_grades
        }

        # The display list is the union of expected + graded + dropped
        # so a graded course always surfaces even if registration data
        # is incomplete (legacy seeds, manual data entry). Each course
        # gets exactly one row; its classification picks the most
        # specific applicable flag (dropped > graded > ungraded).
        display_course_ids = (
            expected_courses
            | set(graded_by_course.keys())
            | dropped_courses
        )

        term_rows: list[StudentTermGrade] = []
        for course_id in display_course_ids:
            course = courses_by_id.get(course_id)
            if course is None:
                continue
            grade = graded_by_course.get(course_id)
            is_dropped = course_id in dropped_courses
            is_added = course_id in added_courses
            # A course is "ungraded" only if the student is expected to
            # take it (registered or added, not dropped) and no grade
            # exists yet. Dropped courses are never ungraded; they're
            # done from the student's perspective.
            is_ungraded = (
                not is_dropped
                and grade is None
                and course_id in expected_courses
            )
            term_rows.append(StudentTermGrade(
                course_id=course.id,
                course_code=course.code,
                course_title=course.title,
                credit_hours=grade.credit_hours if grade else course.credit_hours,
                letter_grade=grade.letter_grade if grade else None,
                numeric_score=grade.numeric_score if grade else None,
                grade_points=grade.grade_points if grade else None,
                is_dropped=is_dropped,
                is_added_via_drop=is_added,
                is_ungraded=is_ungraded,
            ))

        term_rows.sort(key=lambda r: r.course_code)

        expected_count = len(expected_courses)
        ungraded_count = sum(1 for r in term_rows if r.is_ungraded)
        has_ungraded = ungraded_count > 0

        # ── SGPA / CGPA from snapshots only ──
        if existing_standing is not None:
            sgpa = existing_standing.sgpa
            cgpa = existing_standing.cgpa
            term_credit = existing_standing.term_credit_hours
            cumulative_credit = existing_standing.cumulative_credit_hours
            f_count = existing_standing.f_count_term
            f_credit = existing_standing.f_credit_total_term
        else:
            sgpa = None
            cgpa = None
            term_credit = 0
            cumulative_credit = 0
            f_count = 0
            f_credit = 0

        prior_cgpa: Optional[float] = None
        prior_cumulative_credit: Optional[int] = None
        prior_term_name: Optional[str] = None
        if prior_standing is not None:
            prior, prior_term = prior_standing
            prior_cgpa = prior.cgpa
            prior_cumulative_credit = prior.cumulative_credit_hours
            prior_term_name = prior_term.term_name
            # Carry-over only flows into the dedicated prior_* fields.
            # ``cgpa`` stays null until compute runs for this term —
            # the snapshot is the only legitimate source.

        # ── Article-91 evaluation context (cheap to derive live) ──
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
            prior_cgpa=prior_cgpa,
            prior_cumulative_credit_hours=prior_cumulative_credit,
            prior_term_name=prior_term_name,
            is_first_semester=is_first_semester,
            is_first_year=is_first_year,
            has_incomplete_marks=has_incomplete,
            expected_course_count=expected_count,
            ungraded_count=ungraded_count,
            has_ungraded_courses=has_ungraded,
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

    # ══════════════════════════════════════════════════════════
    #  PR C2 — Compute / Authorise / Override
    # ══════════════════════════════════════════════════════════

    async def compute_term_standing(
        self,
        *,
        user_id: uuid.UUID,
        term_id: uuid.UUID,
        department: Optional[str] = None,
        section_id: Optional[uuid.UUID] = None,
    ) -> tuple[list[StandingComputeRow], int, int, list["PendingGradesRow"]]:
        """
        Run the Article-91 rules engine over every cohort member in
        scope and upsert the proposal into ``academic_standings``.

        Scope filters:
          * ``term_id`` — required.
          * ``department`` — narrows to one department's sections.
          * ``section_id`` — narrows to one section.

        Idempotent: re-running upserts the same row and bumps its
        ``computed_at``. Already-authorised rows (final_status set)
        are SKIPPED unless the officer explicitly resets them — the
        agent never overwrites an authorised decision.

        All-graded gate: a student whose registered (or added-via-
        drop) course set is not fully covered by AUTHORISED Grade
        rows for the term is SKIPPED and reported in the returned
        pending list. No AcademicStanding row is written for these
        students — the DH must wait for grading to complete and
        re-run.

        Returns (rows, computed_count, skipped_count, pending_rows).
        """
        await self._resolve_dh_or_403(user_id)
        term = await self._resolve_term_or_404(term_id)

        # Collect cohort registrations.
        stmt = (
            select(Registration, Student)
            .join(Student, Student.id == Registration.student_id)
            .where(
                Registration.term_id == term_id,
                Registration.status.in_(_ATTENDING_STATES),
                Registration.is_deleted == False,  # noqa: E712
                Student.is_deleted == False,  # noqa: E712
                Student.enrollment_status == EnrollmentStatus.ACTIVE,
            )
        )
        if section_id is not None:
            stmt = stmt.where(Registration.section_id == section_id)
        if department is not None:
            stmt = (
                stmt
                .join(Section, Section.id == Registration.section_id)
                .where(Section.department == department)
            )
        cohort = (await self.db.execute(stmt.order_by(Student.student_id.asc()))).all()

        if not cohort:
            return [], 0, 0, []

        # Expected-course set per registration: registered (non-dropped)
        # ∪ added-via-drop. Used by the all-graded gate below.
        reg_ids = [r.id for r, _s in cohort]
        rc_rows = (await self.db.execute(
            select(RegistrationCourse)
            .where(RegistrationCourse.registration_id.in_(reg_ids))
        )).scalars().all()
        original_by_reg: dict[uuid.UUID, set[uuid.UUID]] = {}
        dropped_by_reg: dict[uuid.UUID, set[uuid.UUID]] = {}
        for rc in rc_rows:
            original_by_reg.setdefault(rc.registration_id, set()).add(rc.course_id)
            if rc.is_dropped:
                dropped_by_reg.setdefault(rc.registration_id, set()).add(rc.course_id)
        add_rows = (await self.db.execute(
            select(
                StudentScheduleAddition.registration_id,
                StudentScheduleAddition.course_id,
            )
            .where(StudentScheduleAddition.registration_id.in_(reg_ids))
            .distinct()
        )).all()
        added_by_reg: dict[uuid.UUID, set[uuid.UUID]] = {}
        for r_id, c_id in add_rows:
            added_by_reg.setdefault(r_id, set()).add(c_id)
        expected_by_reg: dict[uuid.UUID, set[uuid.UUID]] = {
            r_id: (original_by_reg.get(r_id, set())
                   - dropped_by_reg.get(r_id, set()))
                  | added_by_reg.get(r_id, set())
            for r_id, _s in (
                (r.id, s) for r, s in cohort
            )
        }

        student_ids = [s.id for _r, s in cohort]
        grades = (await self.db.execute(
            select(Grade).where(
                Grade.student_id.in_(student_ids),
                Grade.status == GradeSubmissionStatus.AUTHORISED,
                Grade.is_deleted == False,  # noqa: E712
            )
        )).scalars().all()

        # Standing for term T is reproducible: it must reflect the
        # student's record as of the END of term T. Any grade from a
        # term whose start_date is strictly AFTER the target term's
        # start_date is "in the future" relative to this standing and
        # must be excluded from the CGPA basis. Otherwise re-computing
        # a past term's standing later (after more grades land) would
        # silently change the answer.
        grade_term_ids = {g.term_id for g in grades}
        grade_term_rows = (
            (await self.db.execute(
                select(AcademicTerm).where(AcademicTerm.id.in_(grade_term_ids))
            )).scalars().all()
            if grade_term_ids else []
        )
        term_start_by_id = {t.id: t.start_date for t in grade_term_rows}
        cutoff = term.start_date
        grades = [
            g for g in grades
            if term_start_by_id.get(g.term_id, cutoff) <= cutoff
        ]

        # Course catalog covers graded courses AND the expected
        # courses, so the pending-grades report can show codes for
        # ungraded courses (which by definition have no Grade row).
        all_course_ids: set[uuid.UUID] = {g.course_id for g in grades}
        for cs in expected_by_reg.values():
            all_course_ids.update(cs)
        courses_by_id: dict[uuid.UUID, Course] = {}
        if all_course_ids:
            course_rows = (await self.db.execute(
                select(Course).where(Course.id.in_(all_course_ids))
            )).scalars().all()
            courses_by_id = {c.id: c for c in course_rows}

        existing_rows = (await self.db.execute(
            select(AcademicStanding).where(
                AcademicStanding.term_id == term_id,
                AcademicStanding.student_id.in_(student_ids),
                AcademicStanding.is_deleted == False,  # noqa: E712
            )
        )).scalars().all()
        existing_by_student = {r.student_id: r for r in existing_rows}

        # Prior-term standing rows (any term before this one) — used
        # to populate ``prior_status`` and ``consecutive_warning_count``.
        prior_rows = (await self.db.execute(
            select(AcademicStanding)
            .where(
                AcademicStanding.student_id.in_(student_ids),
                AcademicStanding.term_id != term_id,
                AcademicStanding.final_status.is_not(None),
                AcademicStanding.is_deleted == False,  # noqa: E712
            )
        )).scalars().all()
        # Map: student_id -> sorted list of (computed_at, final_status, warning_count)
        prior_by_student: dict[uuid.UUID, list[AcademicStanding]] = {}
        for r in prior_rows:
            prior_by_student.setdefault(r.student_id, []).append(r)
        for v in prior_by_student.values():
            v.sort(key=lambda r: r.computed_at)

        agent = self._resolve_agent()
        rows: list[StandingComputeRow] = []
        pending_rows: list[PendingGradesRow] = []
        computed_count = 0
        skipped_count = 0
        for reg, stu in cohort:
            existing = existing_by_student.get(stu.id)
            if existing is not None and existing.final_status is not None:
                # Already authorised — agent does not overwrite.
                skipped_count += 1
                rows.append(StandingComputeRow(
                    standing=AcademicStandingResponse.model_validate(existing),
                    reasons=["Already authorised; agent skipped."],
                    rule_citations=[],
                    narrative=None,
                ))
                continue

            # ── All-graded gate ──
            # The student is skipped (no AcademicStanding row written)
            # when any expected course lacks an AUTHORISED grade for
            # the term. Surfaces in the pending bucket so the DH sees
            # who is still waiting on grading.
            expected = expected_by_reg.get(reg.id, set())
            graded_course_ids = {
                g.course_id for g in grades
                if g.student_id == stu.id and g.term_id == term_id
            }
            ungraded = expected - graded_course_ids
            if expected and ungraded:
                ungraded_codes = sorted(
                    courses_by_id[cid].code
                    for cid in ungraded
                    if cid in courses_by_id
                )
                pending_rows.append(PendingGradesRow(
                    student_id=stu.id,
                    student_number=stu.student_id,
                    full_name=stu.full_name,
                    department=stu.department,
                    expected_course_count=len(expected),
                    graded_course_count=len(expected & graded_course_ids),
                    ungraded_course_codes=ungraded_codes,
                ))
                continue

            ctx, cumulative_credit = self._build_compute_input(
                student=stu,
                term_id=term_id,
                all_grades=[g for g in grades if g.student_id == stu.id],
                courses_by_id=courses_by_id,
                prior_standings=prior_by_student.get(stu.id, []),
            )
            proposal = await agent.propose_standing(
                ctx,
                student_context={
                    "student_id": stu.student_id,
                    "full_name": stu.full_name,
                    "current_semester": stu.current_semester,
                    "department": stu.department,
                },
            )
            standing = await self._upsert_standing(
                student=stu,
                term=term,
                ctx=ctx,
                cumulative_credit_hours=cumulative_credit,
                proposal=proposal,
                existing=existing,
            )
            computed_count += 1
            rows.append(StandingComputeRow(
                standing=AcademicStandingResponse.model_validate(standing),
                reasons=list(proposal.reasons),
                rule_citations=list(proposal.rule_citations),
                narrative=proposal.narrative,
            ))

        await self.db.commit()
        return rows, computed_count, skipped_count, pending_rows

    async def authorise(
        self,
        *,
        user_id: uuid.UUID,
        standing_id: uuid.UUID,
        reason: Optional[str] = None,
    ) -> AcademicStanding:
        """
        Accept the agent's proposed status. Sets ``final_status =
        proposed_status``, stamps the DH on the row, writes an
        ``AUTHORISED`` history entry, and fires a best-effort student
        notification.

        Idempotent: re-authorising an already-authorised row returns
        it unchanged. ``InvalidStateTransitionError`` if the standing
        is on hold (``requires_review=True``) and ``reason`` is empty
        — the officer must justify authorising an incomplete term.
        """
        user = await self._resolve_dh_or_403(user_id)
        standing = await self._resolve_standing_or_404(standing_id)

        if standing.final_status is not None:
            # Idempotent: already authorised.
            return standing

        if standing.requires_review and not (reason and reason.strip()):
            raise InvalidStateTransitionError(
                current="held_for_review",
                target="authorised_without_reason",
            )

        previous = standing.proposed_status
        standing.final_status = standing.proposed_status
        standing.authorised_by_id = user.id
        standing.authorised_at = datetime.now(timezone.utc)
        if reason and reason.strip():
            standing.override_reason = reason.strip()

        self.db.add(AcademicStandingHistory(
            standing_id=standing.id,
            event="AUTHORISED",
            previous_status=previous,
            new_status=standing.final_status,
            changed_by_id=user.id,
            reason=(reason.strip() if reason and reason.strip() else None),
        ))
        await self.db.flush()
        await self.db.commit()
        await self._notify_student_best_effort(
            standing=standing, event="authorised",
        )
        return standing

    async def override(
        self,
        *,
        user_id: uuid.UUID,
        standing_id: uuid.UUID,
        new_status: AcademicStatusType,
        reason: str,
    ) -> AcademicStanding:
        """
        Replace the agent's proposed status with a DH-chosen one.
        Reason is mandatory (enforced upstream by the schema).
        Writes ``OVERRIDDEN`` to history.
        """
        if not reason or not reason.strip():
            raise InvalidAdjustmentRequestError(
                "Override requires a written reason."
            )
        user = await self._resolve_dh_or_403(user_id)
        standing = await self._resolve_standing_or_404(standing_id)

        previous = standing.final_status or standing.proposed_status
        standing.final_status = new_status
        standing.override_reason = reason.strip()
        standing.authorised_by_id = user.id
        standing.authorised_at = datetime.now(timezone.utc)

        self.db.add(AcademicStandingHistory(
            standing_id=standing.id,
            event="OVERRIDDEN",
            previous_status=previous,
            new_status=new_status,
            changed_by_id=user.id,
            reason=reason.strip(),
        ))
        await self.db.flush()
        await self.db.commit()
        await self._notify_student_best_effort(
            standing=standing, event="overridden",
        )
        return standing

    async def get_standing(
        self,
        *,
        user_id: uuid.UUID,
        standing_id: uuid.UUID,
    ) -> AcademicStanding:
        """Officer-or-DH read of one row. 404 if not found."""
        await self._resolve_officer_or_403(user_id)
        return await self._resolve_standing_or_404(standing_id)

    async def list_standings(
        self,
        *,
        user_id: uuid.UUID,
        term_id: Optional[uuid.UUID] = None,
        department: Optional[str] = None,
        student_id: Optional[uuid.UUID] = None,
        only_pending: bool = False,
        requires_review: Optional[bool] = None,
        proposed_status: Optional[AcademicStatusType] = None,
        final_status: Optional[AcademicStatusType] = None,
    ) -> list[StandingQueueEntry]:
        """
        Flexible listing of standing rows for officer + DH browsing.

        Filter knobs (all optional, AND-composed):
          * ``term_id`` / ``department`` / ``student_id`` — scope.
          * ``only_pending`` — narrow to rows where
            ``final_status IS NULL`` (the workflow queue).
          * ``requires_review`` — narrow to held-for-review rows
            (or explicitly exclude them).
          * ``proposed_status`` / ``final_status`` — exact-match
            filter on the enum columns.

        Sorted oldest-first by ``computed_at`` so workflow callers
        drain in computation order; broader audits get a stable
        chronological view.
        """
        await self._resolve_officer_or_403(user_id)
        caller_dept = await self._caller_dh_department(user_id)
        # A DH is always scoped to their department, regardless of
        # what the request asked for.
        effective_department = caller_dept or department

        stmt = (
            select(AcademicStanding, Student, AcademicTerm)
            .join(Student, Student.id == AcademicStanding.student_id)
            .join(AcademicTerm, AcademicTerm.id == AcademicStanding.term_id)
            .where(AcademicStanding.is_deleted == False)  # noqa: E712
            .order_by(AcademicStanding.computed_at.asc())
        )
        if only_pending:
            stmt = stmt.where(AcademicStanding.final_status.is_(None))
        if term_id is not None:
            stmt = stmt.where(AcademicStanding.term_id == term_id)
        if effective_department is not None:
            stmt = stmt.where(
                AcademicStanding.department == effective_department,
            )
        if student_id is not None:
            stmt = stmt.where(AcademicStanding.student_id == student_id)
        if requires_review is not None:
            stmt = stmt.where(
                AcademicStanding.requires_review == requires_review
            )
        if proposed_status is not None:
            stmt = stmt.where(
                AcademicStanding.proposed_status == proposed_status
            )
        if final_status is not None:
            stmt = stmt.where(
                AcademicStanding.final_status == final_status
            )
        rows = (await self.db.execute(stmt)).all()

        return [
            StandingQueueEntry(
                id=standing.id,
                student_id=student.id,
                student_number=student.student_id,
                full_name=student.full_name,
                term_id=term.id,
                term_name=term.term_name,
                department=standing.department,
                sgpa=standing.sgpa,
                cgpa=standing.cgpa,
                proposed_status=standing.proposed_status,
                final_status=standing.final_status,
                requires_review=standing.requires_review,
                computed_at=standing.computed_at,
                authorised_at=standing.authorised_at,
            )
            for standing, student, term in rows
        ]

    async def list_queue(
        self,
        *,
        user_id: uuid.UUID,
        term_id: Optional[uuid.UUID] = None,
        department: Optional[str] = None,
        only_pending: bool = True,
    ) -> list[StandingQueueEntry]:
        """
        Backwards-compatible queue endpoint helper. Delegates to
        :meth:`list_standings` with the workflow-queue defaults
        (``only_pending=True``).
        """
        return await self.list_standings(
            user_id=user_id,
            term_id=term_id,
            department=department,
            only_pending=only_pending,
        )

    async def batch_authorise(
        self,
        *,
        user_id: uuid.UUID,
        standing_ids: list[uuid.UUID],
        reason: Optional[str] = None,
    ) -> BatchAuthoriseResponse:
        """
        Authorise many standing rows in one call. Per-row semantics
        mirror :meth:`authorise`:

          * Already-authorised rows → ``ALREADY_AUTHORISED`` (no-op).
          * Held-for-review (``requires_review=True``) rows with no
            shared reason → ``HELD_NEEDS_REASON`` (skipped; DH must
            handle individually).
          * Unknown IDs → ``NOT_FOUND``.
          * Otherwise → ``AUTHORISED``.

        Single commit at the end so the entire batch is one DB
        transaction. The shared ``reason`` applies to every
        held-for-review row that the DH wants to clear in the batch;
        clean proposals ignore it. Best-effort student notifications
        fire after the commit.
        """
        user = await self._resolve_dh_or_403(user_id)

        normalised_reason = (reason.strip() if reason else "") or None

        # Bulk-load every requested standing in one query, then index
        # by id so we can detect NOT_FOUND in O(1).
        unique_ids = list(dict.fromkeys(standing_ids))
        rows = (await self.db.execute(
            select(AcademicStanding).where(
                AcademicStanding.id.in_(unique_ids),
                AcademicStanding.is_deleted == False,  # noqa: E712
            )
        )).scalars().all()
        by_id = {r.id: r for r in rows}

        now = datetime.now(timezone.utc)
        authorised: list[AcademicStanding] = []
        outcomes: list[BatchAuthoriseOutcome] = []
        counts = Counter()

        for sid in unique_ids:
            standing = by_id.get(sid)
            if standing is None:
                outcomes.append(BatchAuthoriseOutcome(
                    standing_id=sid,
                    status="NOT_FOUND",
                    message="No standing row with that id.",
                ))
                counts["NOT_FOUND"] += 1
                continue

            if standing.final_status is not None:
                outcomes.append(BatchAuthoriseOutcome(
                    standing_id=sid,
                    status="ALREADY_AUTHORISED",
                    final_status=standing.final_status,
                    message="Row already authorised; no change applied.",
                ))
                counts["ALREADY_AUTHORISED"] += 1
                continue

            if standing.requires_review and normalised_reason is None:
                outcomes.append(BatchAuthoriseOutcome(
                    standing_id=sid,
                    status="HELD_NEEDS_REASON",
                    message=(
                        "Standing is held for review (I/NG mark). "
                        "Authorise individually with a written reason."
                    ),
                ))
                counts["HELD_NEEDS_REASON"] += 1
                continue

            previous = standing.proposed_status
            standing.final_status = standing.proposed_status
            standing.authorised_by_id = user.id
            standing.authorised_at = now
            if standing.requires_review and normalised_reason is not None:
                standing.override_reason = normalised_reason

            self.db.add(AcademicStandingHistory(
                standing_id=standing.id,
                event="AUTHORISED",
                previous_status=previous,
                new_status=standing.final_status,
                changed_by_id=user.id,
                reason=normalised_reason if standing.requires_review else None,
            ))

            outcomes.append(BatchAuthoriseOutcome(
                standing_id=sid,
                status="AUTHORISED",
                final_status=standing.final_status,
            ))
            counts["AUTHORISED"] += 1
            authorised.append(standing)

        await self.db.flush()
        await self.db.commit()

        # Best-effort notifications, post-commit so a notification
        # failure can't roll back the authorisations.
        for standing in authorised:
            await self._notify_student_best_effort(
                standing=standing, event="authorised",
            )

        return BatchAuthoriseResponse(
            requested_count=len(unique_ids),
            authorised_count=counts["AUTHORISED"],
            already_authorised_count=counts["ALREADY_AUTHORISED"],
            held_needs_reason_count=counts["HELD_NEEDS_REASON"],
            not_found_count=counts["NOT_FOUND"],
            rows=outcomes,
        )

    # ── Helpers — compute input + upsert + notify ───────────────

    def _build_compute_input(
        self,
        *,
        student: Student,
        term_id: uuid.UUID,
        all_grades: list[Grade],
        courses_by_id: dict[uuid.UUID, Course],
        prior_standings: list[AcademicStanding],
    ) -> tuple[StandingComputeInput, int]:
        """
        Compose the rules-engine input from pre-loaded data.

        Returns ``(input, cumulative_credit_hours)``. The rules engine
        only needs the input; the cumulative credit total is returned
        separately so the service layer can persist it on the
        AcademicStanding row without polluting the rules contract.
        """
        del student  # not used; signature kept for clarity at call site
        term_grades = [g for g in all_grades if g.term_id == term_id]
        prior_grades = [g for g in all_grades if g.term_id != term_id]

        # SGPA over grades counting toward CGPA only.
        sgpa_grades = [
            g for g in term_grades if counts_toward_cgpa(g.letter_grade)
        ]
        term_credit = sum(g.credit_hours for g in sgpa_grades)
        sgpa_points = sum(
            (g.grade_points if g.grade_points is not None
             else (points_for(g.letter_grade) or 0.0) * g.credit_hours)
            for g in sgpa_grades
        )
        sgpa = round(sgpa_points / term_credit, 4) if term_credit > 0 else None

        cgpa_grades = [
            g for g in all_grades if counts_toward_cgpa(g.letter_grade)
        ]
        cum_credit = sum(g.credit_hours for g in cgpa_grades)
        cgpa_points = sum(
            (g.grade_points if g.grade_points is not None
             else (points_for(g.letter_grade) or 0.0) * g.credit_hours)
            for g in cgpa_grades
        )
        cgpa = (
            round(cgpa_points / cum_credit, 4) if cum_credit > 0 else None
        )

        f_count = sum(
            1 for g in term_grades if g.letter_grade == GradeLetter.F
        )
        f_credit = sum(
            g.credit_hours for g in term_grades
            if g.letter_grade == GradeLetter.F
        )

        prior_term_count = len({g.term_id for g in prior_grades})
        is_first_semester = prior_term_count == 0
        is_first_year = prior_term_count <= 1

        # prior_status + consecutive_warning_count come from the most
        # recent authorised standing row.
        prior_status: Optional[AcademicStatusType] = None
        consecutive_warning = 0
        if prior_standings:
            # Already sorted ascending by computed_at upstream.
            latest = prior_standings[-1]
            prior_status = latest.final_status
            # Run-length count of consecutive WARNING terms ending at
            # the latest authorised row.
            for r in reversed(prior_standings):
                if r.final_status == AcademicStatusType.WARNING:
                    consecutive_warning += 1
                else:
                    break

        rows: list[TermGradeRow] = []
        for g in term_grades:
            course = courses_by_id.get(g.course_id)
            if course is None:
                continue
            rows.append(TermGradeRow(
                course_code=course.code,
                letter=g.letter_grade,
                credit_hours=g.credit_hours,
            ))

        return (
            StandingComputeInput(
                sgpa=sgpa,
                cgpa=cgpa,
                term_grades=tuple(rows),
                term_credit_hours=term_credit,
                f_count=f_count,
                f_credit_total=f_credit,
                is_first_semester=is_first_semester,
                is_first_year=is_first_year,
                prior_status=prior_status,
                consecutive_warning_count=consecutive_warning,
            ),
            cum_credit,
        )

    async def _upsert_standing(
        self,
        *,
        student: Student,
        term: AcademicTerm,
        ctx: StandingComputeInput,
        cumulative_credit_hours: int,
        proposal: StandingProposal,
        existing: Optional[AcademicStanding],
    ) -> AcademicStanding:
        """
        Insert or update the AcademicStanding row from a fresh
        proposal, and append the matching history entry.
        """
        now = datetime.now(timezone.utc)

        if existing is None:
            standing = AcademicStanding(
                student_id=student.id,
                term_id=term.id,
                department=student.department or "",
                sgpa=ctx.sgpa,
                cgpa=ctx.cgpa,
                term_credit_hours=ctx.term_credit_hours,
                cumulative_credit_hours=cumulative_credit_hours,
                f_count_term=ctx.f_count,
                f_credit_total_term=ctx.f_credit_total,
                is_first_semester=ctx.is_first_semester,
                is_first_year=ctx.is_first_year,
                prior_status=ctx.prior_status,
                consecutive_warning_count=ctx.consecutive_warning_count,
                proposed_status=proposal.proposed_status,
                final_status=None,
                requires_review=proposal.requires_review,
                computed_at=now,
                computed_by_agent_id=proposal.agent_id,
            )
            self.db.add(standing)
            await self.db.flush()
            self.db.add(AcademicStandingHistory(
                standing_id=standing.id,
                event="PROPOSED",
                previous_status=None,
                new_status=proposal.proposed_status,
                changed_by_id=None,
                agent_id=proposal.agent_id,
                reason=" ".join(proposal.reasons),
            ))
            return standing

        # Update path — RE_COMPUTED if previously had a proposal.
        previous_status = existing.proposed_status
        existing.sgpa = ctx.sgpa
        existing.cgpa = ctx.cgpa
        existing.term_credit_hours = ctx.term_credit_hours
        existing.cumulative_credit_hours = cumulative_credit_hours
        existing.f_count_term = ctx.f_count
        existing.f_credit_total_term = ctx.f_credit_total
        existing.is_first_semester = ctx.is_first_semester
        existing.is_first_year = ctx.is_first_year
        existing.prior_status = ctx.prior_status
        existing.consecutive_warning_count = ctx.consecutive_warning_count
        existing.proposed_status = proposal.proposed_status
        existing.requires_review = proposal.requires_review
        existing.computed_at = now
        existing.computed_by_agent_id = proposal.agent_id

        self.db.add(AcademicStandingHistory(
            standing_id=existing.id,
            event="RE_COMPUTED",
            previous_status=previous_status,
            new_status=proposal.proposed_status,
            changed_by_id=None,
            agent_id=proposal.agent_id,
            reason=" ".join(proposal.reasons),
        ))
        await self.db.flush()
        return existing

    async def _resolve_standing_or_404(
        self, standing_id: uuid.UUID,
    ) -> AcademicStanding:
        standing = (await self.db.execute(
            select(AcademicStanding).where(
                AcademicStanding.id == standing_id,
                AcademicStanding.is_deleted == False,  # noqa: E712
            )
        )).scalar_one_or_none()
        if standing is None:
            raise EntityNotFoundError("AcademicStanding", str(standing_id))
        return standing

    # ══════════════════════════════════════════════════════════
    #  PR C3 — Student-facing view
    # ══════════════════════════════════════════════════════════

    async def _resolve_student_or_403(
        self, user_id: uuid.UUID,
    ) -> Student:
        """
        Look up the Student row for the calling User. Raises
        :class:`StudentProfileRequiredError` (mapped to HTTP 403) when
        the caller has no student profile. Mirrors the helper used
        by :class:`StudentTranscriptService`.
        """
        student = (await self.db.execute(
            select(Student).where(
                Student.user_id == user_id,
                Student.is_deleted == False,  # noqa: E712
            )
        )).scalar_one_or_none()
        if student is None:
            raise StudentProfileRequiredError()
        return student

    async def get_my_standings(
        self, *, user_id: uuid.UUID,
    ) -> StudentStandingTranscriptResponse:
        """
        Every AUTHORISED standing the calling student has, newest-
        first by the term's start date. Pending (final_status NULL)
        rows are invisible — students only see official decisions.
        """
        student = await self._resolve_student_or_403(user_id)

        rows = (await self.db.execute(
            select(AcademicStanding, AcademicTerm)
            .join(AcademicTerm, AcademicTerm.id == AcademicStanding.term_id)
            .where(
                AcademicStanding.student_id == student.id,
                AcademicStanding.final_status.is_not(None),
                AcademicStanding.is_deleted == False,  # noqa: E712
            )
            .order_by(AcademicTerm.start_date.desc())
        )).all()

        # One bulk history query to grab the most-recent reason per
        # standing_id (used as the student-facing explanation when
        # there's no override_reason).
        standing_ids = [s.id for s, _t in rows]
        latest_reason_by_standing: dict[uuid.UUID, Optional[str]] = {}
        if standing_ids:
            hist_rows = (await self.db.execute(
                select(AcademicStandingHistory)
                .where(
                    AcademicStandingHistory.standing_id.in_(standing_ids),
                    AcademicStandingHistory.event.in_(
                        ("PROPOSED", "RE_COMPUTED")
                    ),
                )
                .order_by(AcademicStandingHistory.created_at.desc())
            )).scalars().all()
            for h in hist_rows:
                # First time we see a standing_id in this DESC list,
                # that's the latest reason.
                if h.standing_id not in latest_reason_by_standing:
                    latest_reason_by_standing[h.standing_id] = h.reason

        terms: list[StudentStandingResponse] = [
            self._to_student_view(
                standing=standing,
                term=term,
                fallback_reason=latest_reason_by_standing.get(standing.id),
            )
            for standing, term in rows
        ]
        current_status = terms[0].status if terms else None
        return StudentStandingTranscriptResponse(
            student_id=student.id,
            student_number=student.student_id,
            full_name=student.full_name,
            current_status=current_status,
            terms=terms,
        )

    async def get_my_term_standing(
        self,
        *,
        user_id: uuid.UUID,
        term_id: uuid.UUID,
    ) -> StudentStandingResponse:
        """
        The calling student's authorised standing for one term.
        Returns 404 if no standing exists yet, or if it hasn't been
        authorised. The "is the term real?" check is implicit — an
        unknown term_id can't have a standing for the student.
        """
        student = await self._resolve_student_or_403(user_id)

        row = (await self.db.execute(
            select(AcademicStanding, AcademicTerm)
            .join(AcademicTerm, AcademicTerm.id == AcademicStanding.term_id)
            .where(
                AcademicStanding.student_id == student.id,
                AcademicStanding.term_id == term_id,
                AcademicStanding.final_status.is_not(None),
                AcademicStanding.is_deleted == False,  # noqa: E712
            )
        )).one_or_none()
        if row is None:
            raise EntityNotFoundError(
                "AcademicStanding", f"student={student.id} term={term_id}",
            )
        standing, term = row

        # Latest rules-engine reason as fallback explanation.
        fallback = (await self.db.execute(
            select(AcademicStandingHistory)
            .where(
                AcademicStandingHistory.standing_id == standing.id,
                AcademicStandingHistory.event.in_(
                    ("PROPOSED", "RE_COMPUTED")
                ),
            )
            .order_by(AcademicStandingHistory.created_at.desc())
            .limit(1)
        )).scalar_one_or_none()

        return self._to_student_view(
            standing=standing,
            term=term,
            fallback_reason=fallback.reason if fallback else None,
        )

    @staticmethod
    def _to_student_view(
        *,
        standing: AcademicStanding,
        term: AcademicTerm,
        fallback_reason: Optional[str],
    ) -> StudentStandingResponse:
        explanation = (
            standing.override_reason
            if standing.override_reason
            else fallback_reason
        )
        return StudentStandingResponse(
            id=standing.id,
            term_id=term.id,
            term_name=term.term_name,
            term_phase=term.phase.value,
            term_start_date=term.start_date,
            term_end_date=term.end_date,
            status=standing.final_status,  # type: ignore[arg-type]
            sgpa=standing.sgpa,
            cgpa=standing.cgpa,
            term_credit_hours=standing.term_credit_hours,
            cumulative_credit_hours=standing.cumulative_credit_hours,
            f_count_term=standing.f_count_term,
            authorised_at=standing.authorised_at,
            explanation=explanation,
        )

    async def _notify_student_best_effort(
        self,
        *,
        standing: AcademicStanding,
        event: str,
    ) -> None:
        """
        Fire-and-forget student notification on authorise / override.
        Any failure is logged and swallowed; the DB transaction must
        not depend on email delivery.
        """
        if self._email_service is None:
            return
        # Resolve the student's auth User for the email address.
        student = (await self.db.execute(
            select(Student).where(Student.id == standing.student_id)
        )).scalar_one_or_none()
        if student is None:
            return
        user = (await self.db.execute(
            select(User).where(User.id == student.user_id)
        )).scalar_one_or_none()
        if user is None or not user.email:
            return

        status_label = (
            standing.final_status.value
            if standing.final_status else standing.proposed_status.value
        )
        try:
            message = EmailMessage(
                to_email=user.email,
                subject=f"Academic standing {status_label}",
                text_body=(
                    f"Dear {student.full_name},\n\n"
                    f"Your academic standing for the term has been "
                    f"{event}. Final status: {status_label}.\n\n"
                    f"Semester GPA: "
                    f"{standing.sgpa if standing.sgpa is not None else 'N/A'}\n"
                    f"Cumulative GPA: "
                    f"{standing.cgpa if standing.cgpa is not None else 'N/A'}\n\n"
                    "Please contact the registrar for any questions.\n\n"
                    "— AAU Registrar"
                ),
            )
            await self._email_service.send(message)
        except Exception:
            # Notification is best-effort; swallow.
            return
