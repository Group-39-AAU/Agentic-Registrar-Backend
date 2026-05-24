"""
Unified exception queue service.

Joins the three per-source pending queues into one normalised
list. Read-only: each source already has its own resolution
workflow (advisory close, DH grade-batch authorise/reject,
standing authorise/override), and the queue simply makes them
discoverable from one place.

Auth: REGISTRAR_OFFICER (with backing CourseManagementOfficer row) ∨
DEPARTMENT_HEAD ∨ ADMIN. Mirrors the standing browse auth pattern.
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import User
from app.modules.course.exceptions import UnauthorizedActorError
from app.modules.course.exception_queue.schemas import (
    ExceptionQueueEntry, ExceptionSource,
)
from app.modules.course.grading.models import GradeBatch
from app.modules.course.models import (
    AcademicTerm, AdvisoryRecommendation, Course, CourseManagementOfficer,
    Instructor, Section, Student,
)
from app.modules.course.standing.models import AcademicStanding
from app.shared.enums import (
    GradeSubmissionStatus, OfficerRole, RiskStatus, UserRole,
)


class ExceptionQueueService:
    """Read-only composer for the unified exception queue."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def _resolve_officer_or_403(self, user_id: uuid.UUID) -> User:
        """Same gate the standing browse uses."""
        user = (await self.db.execute(
            select(User).where(User.id == user_id)
        )).scalar_one_or_none()
        if user is None:
            raise UnauthorizedActorError("Calling user not found.")
        if user.role == UserRole.ADMIN:
            return user
        if user.role != UserRole.REGISTRAR_OFFICER:
            raise UnauthorizedActorError(
                "Only officers (or admins) may view the exception queue."
            )
        officer = (await self.db.execute(
            select(CourseManagementOfficer).where(
                CourseManagementOfficer.user_id == user_id,
                CourseManagementOfficer.is_deleted == False,  # noqa: E712
            )
        )).scalar_one_or_none()
        if officer is None or officer.role not in {
            OfficerRole.REGISTRAR_OFFICER, OfficerRole.DEPARTMENT_HEAD,
        }:
            raise UnauthorizedActorError(
                "Calling user has no Course Management officer profile."
            )
        return user

    async def list_pending(
        self,
        *,
        user_id: uuid.UUID,
        sources: Optional[set[ExceptionSource]] = None,
        term_id: Optional[uuid.UUID] = None,
        student_id: Optional[uuid.UUID] = None,
    ) -> list[ExceptionQueueEntry]:
        """
        Join over the three pending sources and return a single
        chronologically sorted list (newest-first). Filters
        compose: ``sources`` narrows which queues to query;
        ``term_id`` / ``student_id`` scope each query.
        """
        await self._resolve_officer_or_403(user_id)

        wanted = sources or {
            ExceptionSource.ADVISORY,
            ExceptionSource.GRADING,
            ExceptionSource.STANDING,
        }

        entries: list[ExceptionQueueEntry] = []
        if ExceptionSource.ADVISORY in wanted:
            entries.extend(await self._load_advisory(
                term_id=term_id, student_id=student_id,
            ))
        if ExceptionSource.GRADING in wanted:
            entries.extend(await self._load_grading(
                term_id=term_id, student_id=student_id,
            ))
        if ExceptionSource.STANDING in wanted:
            entries.extend(await self._load_standing(
                term_id=term_id, student_id=student_id,
            ))

        entries.sort(key=lambda e: e.created_at, reverse=True)
        return entries

    # ── Source loaders ──────────────────────────────────────────

    async def _load_advisory(
        self,
        *,
        term_id: Optional[uuid.UUID],
        student_id: Optional[uuid.UUID],
    ) -> list[ExceptionQueueEntry]:
        stmt = (
            select(AdvisoryRecommendation, Student, AcademicTerm)
            .join(Student, Student.id == AdvisoryRecommendation.student_id)
            .join(AcademicTerm, AcademicTerm.id == AdvisoryRecommendation.term_id)
            .where(
                AdvisoryRecommendation.requires_officer_review == True,  # noqa: E712
                AdvisoryRecommendation.reviewed_at.is_(None),
            )
        )
        if term_id is not None:
            stmt = stmt.where(AdvisoryRecommendation.term_id == term_id)
        if student_id is not None:
            stmt = stmt.where(AdvisoryRecommendation.student_id == student_id)
        rows = (await self.db.execute(stmt)).all()

        out: list[ExceptionQueueEntry] = []
        for rec, student, term in rows:
            out.append(ExceptionQueueEntry(
                source=ExceptionSource.ADVISORY,
                source_id=rec.id,
                student_id=student.id,
                student_number=student.student_id,
                full_name=student.full_name,
                term_id=term.id,
                term_name=term.term_name,
                department=student.department,
                summary=self._advisory_summary(rec),
                deep_link=f"/courses/advisory/recommendations/{rec.id}",
                created_at=rec.created_at,
            ))
        return out

    async def _load_grading(
        self,
        *,
        term_id: Optional[uuid.UUID],
        student_id: Optional[uuid.UUID],
    ) -> list[ExceptionQueueEntry]:
        # student_id is not a meaningful filter on grade batches —
        # batches are per-(section, course) — so honour it only when
        # callers really want the GRADING source filtered. For now we
        # ignore student_id for this source; the UI passes term/dept
        # filters instead.
        del student_id
        stmt = (
            select(GradeBatch, Section, Course, AcademicTerm, Instructor)
            .join(Section, Section.id == GradeBatch.section_id)
            .join(Course, Course.id == GradeBatch.course_id)
            .join(AcademicTerm, AcademicTerm.id == GradeBatch.term_id)
            .join(Instructor, Instructor.id == GradeBatch.instructor_id)
            .where(
                GradeBatch.status == GradeSubmissionStatus.FLAGGED,
                GradeBatch.is_deleted == False,  # noqa: E712
            )
        )
        if term_id is not None:
            stmt = stmt.where(GradeBatch.term_id == term_id)
        rows = (await self.db.execute(stmt)).all()

        out: list[ExceptionQueueEntry] = []
        for batch, section, course, term, _instructor in rows:
            created = batch.submitted_at or batch.created_at
            out.append(ExceptionQueueEntry(
                source=ExceptionSource.GRADING,
                source_id=batch.id,
                student_id=None,
                student_number=None,
                full_name=None,
                term_id=term.id,
                term_name=term.term_name,
                department=section.department,
                summary=(
                    f"Grade batch FLAGGED — {course.code} "
                    f"({section.department} sem {section.semester} "
                    f"section {section.section_code})"
                ),
                deep_link=f"/courses/grading/officer/batches/{batch.id}",
                created_at=created,
            ))
        return out

    async def _load_standing(
        self,
        *,
        term_id: Optional[uuid.UUID],
        student_id: Optional[uuid.UUID],
    ) -> list[ExceptionQueueEntry]:
        stmt = (
            select(AcademicStanding, Student, AcademicTerm)
            .join(Student, Student.id == AcademicStanding.student_id)
            .join(AcademicTerm, AcademicTerm.id == AcademicStanding.term_id)
            .where(
                AcademicStanding.requires_review == True,  # noqa: E712
                AcademicStanding.final_status.is_(None),
                AcademicStanding.is_deleted == False,  # noqa: E712
            )
        )
        if term_id is not None:
            stmt = stmt.where(AcademicStanding.term_id == term_id)
        if student_id is not None:
            stmt = stmt.where(AcademicStanding.student_id == student_id)
        rows = (await self.db.execute(stmt)).all()

        out: list[ExceptionQueueEntry] = []
        for standing, student, term in rows:
            out.append(ExceptionQueueEntry(
                source=ExceptionSource.STANDING,
                source_id=standing.id,
                student_id=student.id,
                student_number=student.student_id,
                full_name=student.full_name,
                term_id=term.id,
                term_name=term.term_name,
                department=standing.department,
                summary=(
                    "Standing held for review (Incomplete / NG mark on "
                    "this term)."
                ),
                deep_link=f"/courses/standing/{standing.id}",
                created_at=standing.computed_at,
            ))
        return out

    @staticmethod
    def _advisory_summary(rec: AdvisoryRecommendation) -> str:
        risk = rec.risk_status.value if rec.risk_status else "UNKNOWN"
        if rec.risk_status == RiskStatus.HIGH:
            return f"HIGH-risk advisory verdict requires officer review."
        return f"Advisory verdict ({risk}) flagged for review."
