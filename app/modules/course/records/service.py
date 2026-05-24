"""
Track C — Records service.

Composes the grade-report and filing-slip JSON payloads from the
existing ledgers (Grade, AcademicStanding, Registration) — no new
storage. Documents are *derived*, not persisted, so they always
reflect the current state of the underlying records. ``document_id``
is a fresh uuid4 per request: the later PDF-signing PR replaces
this with a stable archive id keyed by (student, term, doc-type).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.course.exceptions import (
    EntityNotFoundError, StudentProfileRequiredError,
)
from app.modules.course.models import (
    AcademicTerm, Course, Grade, Registration, RegistrationCourse,
    Section, Student,
)
from app.modules.course.records.schemas import (
    FilingSlipCourseLine, FilingSlipResponse, GradeReportCourseLine,
    GradeReportResponse, RecordStudentHeader, RecordTermHeader,
)
from app.modules.course.standing.models import (
    AcademicStanding, AcademicStandingHistory,
)
from app.shared.enums import GradeSubmissionStatus


class StudentRecordsService:
    """
    Read-only document composer for the calling student. Every
    method resolves the User → Student row first; an officer trying
    to hit these endpoints gets 403 (records belong to the student
    they describe).
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_grade_report(
        self, *, user_id: uuid.UUID, term_id: uuid.UUID,
    ) -> GradeReportResponse:
        """
        Official grade report for one term. Requires the DH to have
        authorised an :class:`AcademicStanding` for the (student,
        term). Returns 404 otherwise — partial-data terms are
        covered by the transcript endpoint.
        """
        student = await self._resolve_student_or_403(user_id)
        term = await self._resolve_term_or_404(term_id)

        standing = (await self.db.execute(
            select(AcademicStanding).where(
                AcademicStanding.student_id == student.id,
                AcademicStanding.term_id == term_id,
                AcademicStanding.final_status.is_not(None),
                AcademicStanding.is_deleted == False,  # noqa: E712
            )
        )).scalar_one_or_none()
        if standing is None:
            raise EntityNotFoundError(
                "AcademicStanding",
                f"student={student.id} term={term_id} (authorised)",
            )

        grades = (await self.db.execute(
            select(Grade).where(
                Grade.student_id == student.id,
                Grade.term_id == term_id,
                Grade.status == GradeSubmissionStatus.AUTHORISED,
                Grade.is_deleted == False,  # noqa: E712
            )
        )).scalars().all()

        course_ids = list({g.course_id for g in grades})
        courses_by_id: dict[uuid.UUID, Course] = {}
        if course_ids:
            course_rows = (await self.db.execute(
                select(Course).where(Course.id.in_(course_ids))
            )).scalars().all()
            courses_by_id = {c.id: c for c in course_rows}

        course_lines: list[GradeReportCourseLine] = []
        for g in grades:
            course = courses_by_id.get(g.course_id)
            if course is None:
                continue
            course_lines.append(GradeReportCourseLine(
                course_code=course.code,
                course_title=course.title,
                credit_hours=g.credit_hours,
                letter_grade=g.letter_grade,
                numeric_score=g.numeric_score,
                grade_points=g.grade_points,
            ))
        course_lines.sort(key=lambda c: c.course_code)

        explanation = standing.override_reason
        if not explanation:
            latest_history = (await self.db.execute(
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
            if latest_history is not None:
                explanation = latest_history.reason

        return GradeReportResponse(
            document_id=uuid.uuid4(),
            generated_at=datetime.now(timezone.utc),
            student=self._student_header(student),
            term=self._term_header(term),
            courses=course_lines,
            term_gpa=standing.sgpa,
            cumulative_gpa=standing.cgpa,
            term_credit_hours=standing.term_credit_hours,
            cumulative_credit_hours=standing.cumulative_credit_hours,
            academic_status=standing.final_status,  # type: ignore[arg-type]
            academic_status_authorised_at=standing.authorised_at,  # type: ignore[arg-type]
            explanation=explanation,
        )

    async def get_filing_slip(
        self, *, user_id: uuid.UUID, term_id: uuid.UUID,
    ) -> FilingSlipResponse:
        """
        Registration-support document. Requires the student to have
        a :class:`Registration` for ``term_id`` (in any non-cancelled
        state — the slip is also useful during ``REGISTRATION_OPEN``
        and ``PAYMENT_HOLD``). 404 if no registration exists.
        """
        student = await self._resolve_student_or_403(user_id)
        term = await self._resolve_term_or_404(term_id)

        registration = (await self.db.execute(
            select(Registration).where(
                Registration.student_id == student.id,
                Registration.term_id == term_id,
                Registration.is_deleted == False,  # noqa: E712
            )
        )).scalar_one_or_none()
        if registration is None:
            raise EntityNotFoundError(
                "Registration",
                f"student={student.id} term={term_id}",
            )

        # Section (cohort) for the registration.
        section_code: Optional[str] = None
        if registration.section_id is not None:
            section = (await self.db.execute(
                select(Section).where(Section.id == registration.section_id)
            )).scalar_one_or_none()
            section_code = section.section_code if section else None

        rc_rows = (await self.db.execute(
            select(RegistrationCourse).where(
                RegistrationCourse.registration_id == registration.id,
            )
        )).scalars().all()
        course_ids = [rc.course_id for rc in rc_rows]
        courses_by_id: dict[uuid.UUID, Course] = {}
        if course_ids:
            course_rows = (await self.db.execute(
                select(Course).where(Course.id.in_(course_ids))
            )).scalars().all()
            courses_by_id = {c.id: c for c in course_rows}

        course_lines: list[FilingSlipCourseLine] = []
        active_credit = 0
        for rc in rc_rows:
            course = courses_by_id.get(rc.course_id)
            if course is None:
                continue
            course_lines.append(FilingSlipCourseLine(
                course_code=course.code,
                course_title=course.title,
                credit_hours=course.credit_hours,
                is_dropped=rc.is_dropped,
            ))
            if not rc.is_dropped:
                active_credit += course.credit_hours
        course_lines.sort(key=lambda c: c.course_code)

        # Carry-over: most recent AUTHORISED standing across any term
        # whose end_date is on or before this term's start_date.
        carry = (await self.db.execute(
            select(AcademicStanding, AcademicTerm)
            .join(AcademicTerm, AcademicTerm.id == AcademicStanding.term_id)
            .where(
                AcademicStanding.student_id == student.id,
                AcademicStanding.final_status.is_not(None),
                AcademicStanding.is_deleted == False,  # noqa: E712
                AcademicTerm.start_date < term.start_date,
            )
            .order_by(AcademicTerm.start_date.desc())
            .limit(1)
        )).one_or_none()

        last_status = carry[0].final_status if carry else None
        last_term_name = carry[1].term_name if carry else None

        return FilingSlipResponse(
            document_id=uuid.uuid4(),
            generated_at=datetime.now(timezone.utc),
            student=self._student_header(student),
            term=self._term_header(term),
            registration_status=registration.status,
            sponsorship_type=registration.sponsorship_type.value,
            section_code=section_code,
            courses=course_lines,
            total_credit_hours=active_credit,
            payment_reference=registration.payment_reference,
            finalised_at=registration.finalised_at,
            last_authorised_status=last_status,
            last_authorised_term_name=last_term_name,
        )

    # ── Helpers ─────────────────────────────────────────────────

    async def _resolve_student_or_403(
        self, user_id: uuid.UUID,
    ) -> Student:
        student = (await self.db.execute(
            select(Student).where(
                Student.user_id == user_id,
                Student.is_deleted == False,  # noqa: E712
            )
        )).scalar_one_or_none()
        if student is None:
            raise StudentProfileRequiredError()
        return student

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

    @staticmethod
    def _student_header(student: Student) -> RecordStudentHeader:
        return RecordStudentHeader(
            student_id=student.id,
            student_number=student.student_id,
            full_name=student.full_name,
            department=student.department,
            current_semester=student.current_semester,
        )

    @staticmethod
    def _term_header(term: AcademicTerm) -> RecordTermHeader:
        return RecordTermHeader(
            term_id=term.id,
            term_name=term.term_name,
            term_phase=term.phase.value,
            term_start_date=term.start_date,
            term_end_date=term.end_date,
        )
