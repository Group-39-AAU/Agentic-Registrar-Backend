"""
Track B (Grading) — Student transcript service (PR 4).

Read-only surface for the calling student. Only ``AUTHORISED``
``Grade`` rows surface in the transcript — anything still SUBMITTED,
FLAGGED, REJECTED, or DRAFT is invisible to the student until a DH
authorises it.

For each AUTHORISED course, the response also includes the
breakdown + per-component raw scores when a Track-B ``GradeBatch``
exists for that (section, course). Legacy ``Grade`` rows seeded
before Track B (no associated batch) carry ``has_breakdown=False``
and an empty components list — the letter and numeric still come
through, just without the assessment detail.
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.course.exceptions import (
    EntityNotFoundError, StudentProfileRequiredError,
)
from app.modules.course.grading.models import (
    AssessmentBreakdown, AssessmentComponent, GradeBatch,
    StudentComponentScore,
)
from app.modules.course.grading.schemas import (
    TranscriptComponentScore, TranscriptCourseEntry,
    TranscriptResponse, TranscriptTermEntry,
)
from app.modules.course.models import (
    AcademicTerm, Course, Grade, Student,
)
from app.modules.course.standing.models import AcademicStanding
from app.shared.enums import GradeSubmissionStatus


class StudentTranscriptService:
    """
    Resolves the calling user's Student row and serves AUTHORISED
    grades from the Track A ``grades`` table, enriched with Track B
    breakdown data when available.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def _resolve_student_or_403(
        self, user_id: uuid.UUID,
    ) -> Student:
        student = (
            await self.db.execute(
                select(Student).where(
                    Student.user_id == user_id,
                    Student.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()
        if student is None:
            raise StudentProfileRequiredError()
        return student

    # ── All-term transcript ─────────────────────────────────────

    async def get_transcript(
        self,
        *,
        user_id: uuid.UUID,
    ) -> TranscriptResponse:
        """
        Every AUTHORISED ``Grade`` for the calling student, grouped
        by term (most-recent first inside the list of terms). Per-
        term GPA = sum(grade_points) / sum(credit_hours); the same
        formula yields the overall CGPA across all terms.
        """
        student = await self._resolve_student_or_403(user_id)
        grades = await self._load_authorised_grades(student.id)
        return await self._compose_transcript(student, grades)

    async def get_transcript_by_student_id(
        self,
        *,
        student_id: uuid.UUID,
    ) -> TranscriptResponse:
        """
        Same transcript composition as :meth:`get_transcript`, but
        resolves the target by the ``Student.id`` directly instead of
        the caller's ``user_id``. Used by officer-facing endpoints
        (DH add/drop review, registrar lookup) where the caller is
        looking at another user's transcript — the auth check lives
        in the caller, not here.
        """
        student = (
            await self.db.execute(
                select(Student).where(
                    Student.id == student_id,
                    Student.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()
        if student is None:
            raise EntityNotFoundError("Student", str(student_id))
        grades = await self._load_authorised_grades(student.id)
        return await self._compose_transcript(student, grades)

    # ── Per-term grades ─────────────────────────────────────────

    async def get_term_grades(
        self,
        *,
        user_id: uuid.UUID,
        term_id: uuid.UUID,
    ) -> TranscriptTermEntry:
        """One term's AUTHORISED grades."""
        student = await self._resolve_student_or_403(user_id)
        term = (await self.db.execute(
            select(AcademicTerm).where(
                AcademicTerm.id == term_id,
                AcademicTerm.is_deleted == False,  # noqa: E712
            )
        )).scalar_one_or_none()
        if term is None:
            raise EntityNotFoundError("AcademicTerm", str(term_id))

        grades = await self._load_authorised_grades(
            student.id, term_id=term_id,
        )
        # Single-term standing lookup so the transcript entry carries
        # the official AAU status when one has been authorised.
        standing = (await self.db.execute(
            select(AcademicStanding).where(
                AcademicStanding.student_id == student.id,
                AcademicStanding.term_id == term_id,
                AcademicStanding.final_status.is_not(None),
                AcademicStanding.is_deleted == False,  # noqa: E712
            )
        )).scalar_one_or_none()
        standings_by_term = (
            {term_id: standing} if standing is not None else {}
        )
        return await self._compose_term_entry(
            term, grades, standings_by_term=standings_by_term,
        )

    # ── Helpers ─────────────────────────────────────────────────

    async def _load_authorised_grades(
        self,
        student_id: uuid.UUID,
        *,
        term_id: Optional[uuid.UUID] = None,
    ) -> list[Grade]:
        stmt = (
            select(Grade)
            .where(
                Grade.student_id == student_id,
                Grade.status == GradeSubmissionStatus.AUTHORISED,
                Grade.is_deleted == False,  # noqa: E712
            )
        )
        if term_id is not None:
            stmt = stmt.where(Grade.term_id == term_id)
        return (await self.db.execute(stmt)).scalars().all()

    async def _compose_transcript(
        self, student: Student, grades: list[Grade],
    ) -> TranscriptResponse:
        # Bucket grades by term, then look up term metadata in one
        # batch query to avoid N+1.
        by_term: dict[uuid.UUID, list[Grade]] = {}
        for g in grades:
            by_term.setdefault(g.term_id, []).append(g)

        if not by_term:
            return TranscriptResponse(
                student_id=student.id,
                student_number=student.student_id,
                full_name=student.full_name,
                terms=[],
                cgpa=None,
                total_credit_hours_completed=0,
            )

        terms = (await self.db.execute(
            select(AcademicTerm).where(
                AcademicTerm.id.in_(by_term.keys()),
            )
        )).scalars().all()
        term_by_id = {t.id: t for t in terms}

        # Bulk-load every authorised AcademicStanding for this student
        # across the terms in the transcript so per-term entries
        # surface the official status without N+1 queries.
        standing_rows = (await self.db.execute(
            select(AcademicStanding).where(
                AcademicStanding.student_id == student.id,
                AcademicStanding.term_id.in_(by_term.keys()),
                AcademicStanding.final_status.is_not(None),
                AcademicStanding.is_deleted == False,  # noqa: E712
            )
        )).scalars().all()
        standings_by_term = {s.term_id: s for s in standing_rows}

        entries: list[TranscriptTermEntry] = []
        for term_id, term_grades in by_term.items():
            term = term_by_id.get(term_id)
            if term is None:  # tombstoned term row
                continue
            entries.append(await self._compose_term_entry(
                term, term_grades, standings_by_term=standings_by_term,
            ))

        # Sort terms newest first (start_date desc).
        entries.sort(key=lambda e: e.term_start_date, reverse=True)

        # CGPA across all entries.
        total_credit = sum(e.total_credit_hours for e in entries)
        weighted = sum(
            (e.term_gpa or 0.0) * e.total_credit_hours for e in entries
        )
        cgpa = (
            round(weighted / total_credit, 4)
            if total_credit > 0 else None
        )

        return TranscriptResponse(
            student_id=student.id,
            student_number=student.student_id,
            full_name=student.full_name,
            terms=entries,
            cgpa=cgpa,
            total_credit_hours_completed=total_credit,
        )

    async def _compose_term_entry(
        self,
        term: AcademicTerm,
        grades: list[Grade],
        *,
        standings_by_term: Optional[dict[uuid.UUID, AcademicStanding]] = None,
    ) -> TranscriptTermEntry:
        # Hydrate course metadata and the optional Track B breakdown
        # in one pass per course id.
        course_ids = list({g.course_id for g in grades})
        courses = (
            (await self.db.execute(
                select(Course).where(Course.id.in_(course_ids))
            )).scalars().all()
            if course_ids else []
        )
        course_by_id = {c.id: c for c in courses}

        # Map (section_id, course_id) → batch + components.
        batch_keys = [(g.section_id, g.course_id) for g in grades if g.section_id]
        course_entries: list[TranscriptCourseEntry] = []
        for g in grades:
            course = course_by_id.get(g.course_id)
            if course is None:
                continue

            components = await self._load_component_breakdown(
                student_id=g.student_id,
                section_id=g.section_id,
                course_id=g.course_id,
            )
            course_entries.append(TranscriptCourseEntry(
                course_id=course.id,
                course_code=course.code,
                course_title=course.title,
                credit_hours=g.credit_hours,
                letter_grade=g.letter_grade,
                numeric_score=g.numeric_score,
                grade_points=g.grade_points,
                has_breakdown=bool(components),
                components=components,
            ))
        # Sort within a term by course code for stable display.
        course_entries.sort(key=lambda c: c.course_code)

        total_credit = sum(c.credit_hours for c in course_entries)
        weighted_points = sum(
            (c.grade_points or 0.0) for c in course_entries
        )
        term_gpa = (
            round(weighted_points / total_credit, 4)
            if total_credit > 0 else None
        )

        # Year-in-program (I–V) derived from the highest curriculum
        # semester represented in this term. After the seed split each
        # term holds exactly one curriculum semester so max == min, but
        # using max keeps the label sensible for legacy data that
        # bundled multiple semesters into a single ``history_term``.
        curriculum_semesters = [c.semester for c in courses]
        year_in_program = (
            (max(curriculum_semesters) + 1) // 2
            if curriculum_semesters else 0
        )

        standing = (
            standings_by_term.get(term.id) if standings_by_term else None
        )
        return TranscriptTermEntry(
            term_id=term.id,
            term_name=term.term_name,
            term_phase=term.phase.value,
            term_start_date=term.start_date,
            term_end_date=term.end_date,
            year_in_program=year_in_program,
            courses=course_entries,
            term_gpa=term_gpa,
            total_credit_hours=total_credit,
            academic_status=(
                standing.final_status if standing is not None else None
            ),
            academic_status_authorised_at=(
                standing.authorised_at if standing is not None else None
            ),
        )

    async def _load_component_breakdown(
        self,
        *,
        student_id: uuid.UUID,
        section_id: Optional[uuid.UUID],
        course_id: uuid.UUID,
    ) -> list[TranscriptComponentScore]:
        """
        Return the per-component scores for this (student, section,
        course). Empty list when no Track B batch backs the grade —
        e.g. legacy ``Grade`` rows seeded before Track B.
        """
        if section_id is None:
            return []
        batch = (await self.db.execute(
            select(GradeBatch).where(
                GradeBatch.section_id == section_id,
                GradeBatch.course_id == course_id,
                GradeBatch.is_deleted == False,  # noqa: E712
            )
        )).scalar_one_or_none()
        if batch is None:
            return []

        breakdown = (await self.db.execute(
            select(AssessmentBreakdown).where(
                AssessmentBreakdown.id == batch.breakdown_id,
            )
        )).scalar_one_or_none()
        if breakdown is None:
            return []

        components = sorted(
            (await self.db.execute(
                select(AssessmentComponent).where(
                    AssessmentComponent.breakdown_id == breakdown.id,
                )
            )).scalars().all(),
            key=lambda c: c.order_index,
        )
        scores = (await self.db.execute(
            select(StudentComponentScore).where(
                StudentComponentScore.batch_id == batch.id,
                StudentComponentScore.student_id == student_id,
            )
        )).scalars().all()
        score_by_component = {s.component_id: s.score for s in scores}

        out: list[TranscriptComponentScore] = []
        for c in components:
            raw = score_by_component.get(c.id)
            contribution = (
                round((raw / c.max_score) * c.weight, 4)
                if raw is not None and c.max_score else None
            )
            out.append(TranscriptComponentScore(
                name=c.name,
                weight=c.weight,
                max_score=c.max_score,
                score=raw,
                weighted_contribution=contribution,
            ))
        return out
