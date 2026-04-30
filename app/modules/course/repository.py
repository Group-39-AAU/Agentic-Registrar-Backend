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

from app.modules.course.models import (
    AcademicTerm, Course, Registration, RegistrationCourse, Student,
)


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
