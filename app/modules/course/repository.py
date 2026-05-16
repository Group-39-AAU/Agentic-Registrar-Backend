"""
Course Management — repository (Track A foundation).

Thin async data-access helpers. Service layer composes these and
owns the transaction boundary. Mirrors the undergraduate repository
pattern.
"""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.course.grade_points import counts_toward_cgpa, is_passing
from app.modules.course.models import (
    AcademicTerm, Course, Grade, Registration, RegistrationCourse, Student,
)
from app.shared.enums import GradeSubmissionStatus, RegistrationStatus


class AcademicTermRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get(self, term_id: uuid.UUID) -> Optional[AcademicTerm]:
        return (
            await self.db.execute(
                select(AcademicTerm).where(
                    AcademicTerm.id == term_id,
                    AcademicTerm.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()

    async def list_all(
        self, *, is_open: Optional[bool] = None,
    ) -> list[AcademicTerm]:
        stmt = select(AcademicTerm).where(
            AcademicTerm.is_deleted == False,  # noqa: E712
        )
        if is_open is not None:
            stmt = stmt.where(AcademicTerm.is_open == is_open)
        stmt = stmt.order_by(AcademicTerm.start_date.asc())
        return list((await self.db.execute(stmt)).scalars().all())

    async def get_open(self) -> Optional[AcademicTerm]:
        """
        The currently-open term. Phase-1 invariant: at most one term
        can be open at a time (officer flips ``is_open`` exclusively
        through TermService.open_window). Returns the most recently
        opened one as a tiebreaker for any drift.
        """
        return (
            await self.db.execute(
                select(AcademicTerm).where(
                    AcademicTerm.is_open == True,  # noqa: E712
                    AcademicTerm.is_deleted == False,  # noqa: E712
                ).order_by(AcademicTerm.start_date.desc())
            )
        ).scalars().first()


class CourseRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get(self, course_id: uuid.UUID) -> Optional[Course]:
        return (
            await self.db.execute(
                select(Course).where(
                    Course.id == course_id,
                    Course.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()

    async def list_by_department(
        self, department: str, *, semester: Optional[int] = None,
    ) -> list[Course]:
        stmt = select(Course).where(
            Course.department == department,
            Course.is_deleted == False,  # noqa: E712
        )
        if semester is not None:
            stmt = stmt.where(Course.semester == semester)
        return list((await self.db.execute(stmt)).scalars().all())


class StudentRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_user_id(self, user_id: uuid.UUID) -> Optional[Student]:
        return (
            await self.db.execute(
                select(Student).where(
                    Student.user_id == user_id,
                    Student.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()

    async def get_by_student_id(self, student_id: str) -> Optional[Student]:
        return (
            await self.db.execute(
                select(Student).where(
                    Student.student_id == student_id,
                    Student.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()


class RegistrationRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get(self, registration_id: uuid.UUID) -> Optional[Registration]:
        return (
            await self.db.execute(
                select(Registration).where(
                    Registration.id == registration_id,
                    Registration.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()

    async def get_for_student_and_term(
        self, student_id: uuid.UUID, term_id: uuid.UUID,
    ) -> Optional[Registration]:
        return (
            await self.db.execute(
                select(Registration).where(
                    Registration.student_id == student_id,
                    Registration.term_id == term_id,
                    Registration.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()

    async def list_for_student(
        self, student_id: uuid.UUID,
    ) -> list[Registration]:
        return list(
            (
                await self.db.execute(
                    select(Registration).where(
                        Registration.student_id == student_id,
                        Registration.is_deleted == False,  # noqa: E712
                    )
                )
            ).scalars().all()
        )

    async def get_course_link(
        self, registration_id: uuid.UUID, course_id: uuid.UUID,
    ) -> Optional[RegistrationCourse]:
        return (
            await self.db.execute(
                select(RegistrationCourse).where(
                    RegistrationCourse.registration_id == registration_id,
                    RegistrationCourse.course_id == course_id,
                )
            )
        ).scalar_one_or_none()

    async def get_active_for_student_in_term(
        self, student_id: uuid.UUID, term_id: uuid.UUID,
    ) -> Optional[Registration]:
        """
        The student's REGISTERED or ADD_DROP_WINDOW registration for
        the given term — i.e. the one the add/drop consult should
        reason against. Returns None if the student has not yet
        finalised registration for this term.
        """
        return (
            await self.db.execute(
                select(Registration).where(
                    Registration.student_id == student_id,
                    Registration.term_id == term_id,
                    Registration.status.in_([
                        RegistrationStatus.REGISTERED,
                        RegistrationStatus.ADD_DROP_WINDOW,
                    ]),
                    Registration.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()

    async def get_draft_for_student_in_term(
        self, student_id: uuid.UUID, term_id: uuid.UUID,
    ) -> Optional[Registration]:
        """
        The student's in-progress draft registration for the term —
        anything that is not yet finalised (REGISTERED, ADD_DROP_WINDOW)
        and is not CANCELLED. Used by the registration-plan consult
        endpoint to read the proposed course list without asking the
        caller for it.
        """
        non_draft = {
            RegistrationStatus.REGISTERED,
            RegistrationStatus.ADD_DROP_WINDOW,
            RegistrationStatus.CANCELLED,
        }
        return (
            await self.db.execute(
                select(Registration).where(
                    Registration.student_id == student_id,
                    Registration.term_id == term_id,
                    Registration.status.notin_(non_draft),
                    Registration.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()


class AddDropRequestRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get(self, request_id: uuid.UUID):
        from app.modules.course.models import AddDropRequest
        return (
            await self.db.execute(
                select(AddDropRequest).where(
                    AddDropRequest.id == request_id,
                    AddDropRequest.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()

    async def list_for_registration(
        self, registration_id: uuid.UUID,
    ):
        from app.modules.course.models import AddDropRequest
        return list(
            (
                await self.db.execute(
                    select(AddDropRequest).where(
                        AddDropRequest.registration_id == registration_id,
                        AddDropRequest.is_deleted == False,  # noqa: E712
                    )
                )
            ).scalars().all()
        )


class AdvisoryRecommendationRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get(self, recommendation_id: uuid.UUID):
        from app.modules.course.models import AdvisoryRecommendation
        return (
            await self.db.execute(
                select(AdvisoryRecommendation).where(
                    AdvisoryRecommendation.id == recommendation_id,
                )
            )
        ).scalar_one_or_none()

    async def list_for_student(
        self, student_id: uuid.UUID,
    ):
        from app.modules.course.models import AdvisoryRecommendation
        return list(
            (
                await self.db.execute(
                    select(AdvisoryRecommendation).where(
                        AdvisoryRecommendation.student_id == student_id,
                    ).order_by(AdvisoryRecommendation.created_at.desc())
                )
            ).scalars().all()
        )

    async def list_high_risk_open(
        self, term_id: uuid.UUID,
    ):
        from app.modules.course.models import AdvisoryRecommendation
        from app.shared.enums import RiskStatus
        return list(
            (
                await self.db.execute(
                    select(AdvisoryRecommendation).where(
                        AdvisoryRecommendation.term_id == term_id,
                        AdvisoryRecommendation.risk_status == RiskStatus.HIGH,
                        AdvisoryRecommendation.requires_officer_review == True,  # noqa: E712
                        AdvisoryRecommendation.reviewed_at.is_(None),
                    ).order_by(AdvisoryRecommendation.created_at.asc())
                )
            ).scalars().all()
        )


class GradeRepository:
    """
    Async helpers over the Track-B-aligned ``grades`` ledger. The
    advisory consult flow uses this to resolve a student's CGPA and
    completed-course set without asking the caller — Track B's grade
    entry pipeline will later be the writer.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_authorised_for_student(
        self, student_id: uuid.UUID,
    ) -> list[Grade]:
        """
        All AUTHORISED grades for a student. Excludes drafts /
        flagged / submitted / rejected because only authorised
        grades count toward CGPA per SRS Course-FR-09.
        """
        return list(
            (
                await self.db.execute(
                    select(Grade).where(
                        Grade.student_id == student_id,
                        Grade.status == GradeSubmissionStatus.AUTHORISED,
                        Grade.is_deleted == False,  # noqa: E712
                    )
                )
            ).scalars().all()
        )

    async def completed_course_ids(
        self, student_id: uuid.UUID,
    ) -> set[uuid.UUID]:
        """
        Course IDs the student has passed (any AUTHORISED grade
        whose letter is at or above D). F, I, NG do not count.
        """
        grades = await self.list_authorised_for_student(student_id)
        return {g.course_id for g in grades if is_passing(g.letter_grade)}

    async def compute_cgpa(
        self, student_id: uuid.UUID,
    ) -> Optional[float]:
        """
        Credit-weighted CGPA over every AUTHORISED grade whose
        letter has a 4.0-scale point value (F counts as 0.0; I and
        NG are excluded — they route through the AcademicStandingAgent
        edge-case path per SDS Table 75).

        Returns ``None`` when the student has no CGPA-eligible
        grades yet (e.g. a fresh first-semester student). Callers
        should treat None as "no academic history" rather than
        substituting 0.0, which would falsely trigger the HIGH-risk
        Warning threshold.
        """
        grades = await self.list_authorised_for_student(student_id)
        eligible = [g for g in grades if counts_toward_cgpa(g.letter_grade)]
        if not eligible:
            return None

        total_points = 0.0
        total_credits = 0
        for g in eligible:
            # Prefer the cached grade_points if the writer set it;
            # otherwise compute on the fly so legacy rows still work.
            from app.modules.course.grade_points import points_for
            pts = (
                g.grade_points
                if g.grade_points is not None
                else (points_for(g.letter_grade) or 0.0) * g.credit_hours
            )
            total_points += pts
            total_credits += g.credit_hours
        if total_credits == 0:
            return None
        return round(total_points / total_credits, 2)
