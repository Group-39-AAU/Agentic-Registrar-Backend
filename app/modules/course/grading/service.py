"""
Track B (Grading) — service layer.

PR 1 owns:

  * ``InstructorGradingService.list_my_section_assignments`` — every
    (section, course) pair the calling instructor teaches in a term,
    derived from ``ClassScheduleSlot`` rows.
  * ``InstructorGradingService.get_section_course_roster`` — the
    effective roster for one (section, course), enforcing the
    instructor's ownership of the slot before any data is returned.

The service uses the same auth-failure / not-found exception classes
already defined for the course module so the router maps them to
HTTP status codes the same way it does for Track A endpoints.
"""
from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.course.exceptions import (
    EntityNotFoundError, UnauthorizedActorError,
)
from app.modules.course.models import (
    ClassScheduleSlot, Course, Instructor, Section,
)
from app.modules.course.grading.roster import (
    derive_section_course_roster, section_exists,
)
from app.modules.course.grading.schemas import (
    InstructorSectionAssignmentResponse, RosterStudentResponse,
    SectionCourseRosterResponse,
)


class InstructorGradingService:
    """
    Read-side service for the grading workflow's instructor surface.
    Resolves the calling user's Instructor profile, materialises the
    list of (section, course) pairs they teach in a term, and serves
    the effective roster of each pair.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Resolve the calling user to an Instructor row ──

    async def _resolve_instructor_or_403(
        self, user_id: uuid.UUID,
    ) -> Instructor:
        """
        Look up the Instructor profile for the calling User. The
        router has already confirmed the user has UserRole.INSTRUCTOR;
        this check covers the rare case where the role is set but
        the profile row was never seeded (e.g. portal misconfig).
        """
        instructor = (
            await self.db.execute(
                select(Instructor).where(
                    Instructor.user_id == user_id,
                    Instructor.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()
        if instructor is None:
            raise UnauthorizedActorError(
                "Calling user has no instructor profile."
            )
        return instructor

    # ── Endpoint 1: "what (section, course) pairs do I teach?" ──

    async def list_my_section_assignments(
        self,
        *,
        user_id: uuid.UUID,
        term_id: uuid.UUID,
    ) -> list[InstructorSectionAssignmentResponse]:
        """
        Every (section, course) pair the calling instructor teaches in
        ``term_id``. Derived from ``ClassScheduleSlot`` rows joined to
        ``Section`` (for the term filter and section metadata) and
        ``Course`` (for course metadata).

        Deduplicated to one entry per (section, course); ``slot_count``
        records how many distinct weekly slots back the assignment.
        """
        instructor = await self._resolve_instructor_or_403(user_id)

        stmt = (
            select(ClassScheduleSlot, Section, Course)
            .join(Section, Section.id == ClassScheduleSlot.section_id)
            .join(Course, Course.id == ClassScheduleSlot.course_id)
            .where(
                ClassScheduleSlot.instructor_id == instructor.id,
                Section.term_id == term_id,
                Section.is_deleted == False,  # noqa: E712
            )
        )
        rows = (await self.db.execute(stmt)).all()

        # Bucket by (section, course); count the slots in each bucket.
        buckets: dict[
            tuple[uuid.UUID, uuid.UUID],
            tuple[Section, Course, int],
        ] = {}
        slot_counts: dict[tuple[uuid.UUID, uuid.UUID], int] = defaultdict(int)
        for _slot, section, course in rows:
            key = (section.id, course.id)
            slot_counts[key] += 1
            buckets[key] = (section, course, 0)

        # Sort: department → semester → section_code → course_code for
        # a stable, human-readable list in the UI.
        def _sort_key(item):
            (sid, cid), (section, course, _) = item
            return (
                section.department,
                section.semester,
                section.section_code,
                course.code,
            )

        return [
            InstructorSectionAssignmentResponse(
                section_id=section.id,
                section_code=section.section_code,
                section_department=section.department,
                section_semester=section.semester,
                course_id=course.id,
                course_code=course.code,
                course_title=course.title,
                course_credit_hours=course.credit_hours,
                term_id=section.term_id,
                slot_count=slot_counts[(section.id, course.id)],
            )
            for (_key, (section, course, _zero)) in sorted(
                buckets.items(), key=_sort_key,
            )
        ]

    # ── Endpoint 2: "what students should I grade in this (S, C)?" ──

    async def get_section_course_roster(
        self,
        *,
        user_id: uuid.UUID,
        section_id: uuid.UUID,
        course_id: uuid.UUID,
    ) -> SectionCourseRosterResponse:
        """
        Effective roster for ``(section_id, course_id)``.

        Authorisation: the caller must own at least one
        ``ClassScheduleSlot`` for this exact pair. This is the
        instructor's "I teach this" gate — a teacher of section A
        cannot peek at section B's roster, even within the same
        course.

        Returns a not-found error if the section does not exist; a
        403 if the section exists but the caller doesn't teach this
        (section, course) pair.
        """
        instructor = await self._resolve_instructor_or_403(user_id)

        if not await section_exists(self.db, section_id):
            raise EntityNotFoundError("Section", str(section_id))

        # Ownership gate — does the caller actually teach this pair?
        ownership_stmt = (
            select(ClassScheduleSlot.id)
            .where(
                ClassScheduleSlot.section_id == section_id,
                ClassScheduleSlot.course_id == course_id,
                ClassScheduleSlot.instructor_id == instructor.id,
            )
            .limit(1)
        )
        owns = (await self.db.execute(ownership_stmt)).scalar_one_or_none()
        if owns is None:
            raise UnauthorizedActorError(
                "You are not assigned to teach this (section, course) pair."
            )

        # Pull the section and course rows for response metadata.
        section = (
            await self.db.execute(
                select(Section).where(Section.id == section_id)
            )
        ).scalar_one()
        course = (
            await self.db.execute(
                select(Course).where(Course.id == course_id)
            )
        ).scalar_one_or_none()
        if course is None:
            raise EntityNotFoundError("Course", str(course_id))

        members = await derive_section_course_roster(
            self.db, section_id=section_id, course_id=course_id,
        )
        original_count = sum(1 for m in members if not m.is_added_via_drop)
        added_count = len(members) - original_count

        return SectionCourseRosterResponse(
            section_id=section.id,
            section_code=section.section_code,
            course_id=course.id,
            course_code=course.code,
            course_title=course.title,
            term_id=section.term_id,
            total=len(members),
            original_count=original_count,
            added_count=added_count,
            students=[
                RosterStudentResponse(
                    student_id=m.student_id,
                    student_number=m.student_number,
                    full_name=m.full_name,
                    current_semester=m.current_semester,
                    registration_id=m.registration_id,
                    is_added_via_drop=m.is_added_via_drop,
                )
                for m in members
            ],
        )
