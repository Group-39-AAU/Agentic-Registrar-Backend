"""
Effective-roster derivation for a (section, course) pair.

The "what students should an instructor grade?" question is exactly:

    ORIGINAL  = students whose Registration.section_id == this section
                AND who have a RegistrationCourse for this course where
                is_dropped == False
                AND whose Registration is in a state that means they
                are actually attending (REGISTERED or ADD_DROP_WINDOW)

    ADDED     = students who have a StudentScheduleAddition row whose
                source_section_id == this section AND whose course_id
                == this course (deduplicated on registration — a 3-credit
                course typically attaches as 3 slot rows from the same
                source section)

    EXCLUDED  = anything else, in particular:
                  - students who originally took the course but dropped
                    it (RegistrationCourse.is_dropped == True)
                  - students whose cohort is this section but who moved
                    a specific course out via add/drop (they show up on
                    a different section's roster instead, via the
                    StudentScheduleAddition table)

The function lives in its own module so the grading service and any
later agent tools can call the same authoritative derivation.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.course.models import (
    Registration, RegistrationCourse, Section, Student,
    StudentScheduleAddition,
)
from app.shared.enums import EnrollmentStatus, RegistrationStatus


# Registration states that mean "this student is actually attending the
# section this term". Drafts and cancelled registrations are excluded
# from the roster because their courses are not real attendance.
_ATTENDING_STATES: frozenset[RegistrationStatus] = frozenset({
    RegistrationStatus.REGISTERED,
    RegistrationStatus.ADD_DROP_WINDOW,
})


@dataclass(frozen=True)
class RosterMember:
    """
    One row of the derived roster. Pure data — no ORM relationships,
    safe to pass between layers.
    """
    student_id: uuid.UUID
    student_number: str
    full_name: str
    current_semester: int
    registration_id: uuid.UUID
    is_added_via_drop: bool


async def derive_section_course_roster(
    db: AsyncSession,
    *,
    section_id: uuid.UUID,
    course_id: uuid.UUID,
) -> list[RosterMember]:
    """
    Compute the effective roster for ``(section_id, course_id)``.

    Returns rows sorted by ``student_number`` ascending so the order
    is stable between calls and human-readable in the UI. ORIGINAL
    members come first because that ordering survives any sort key.

    Raises nothing — an unknown section or course just returns an
    empty list. The caller is responsible for any "does this section
    exist?" 404 handling.
    """
    # ── ORIGINAL members ──
    # Cohort members of this section who still have the course on
    # their (non-dropped) registration courses.
    original_stmt = (
        select(Registration, Student, RegistrationCourse)
        .join(Student, Student.id == Registration.student_id)
        .join(
            RegistrationCourse,
            RegistrationCourse.registration_id == Registration.id,
        )
        .where(
            Registration.section_id == section_id,
            Registration.status.in_(_ATTENDING_STATES),
            Registration.is_deleted == False,  # noqa: E712
            Student.is_deleted == False,  # noqa: E712
            Student.enrollment_status == EnrollmentStatus.ACTIVE,
            RegistrationCourse.course_id == course_id,
            RegistrationCourse.is_dropped == False,  # noqa: E712
        )
    )
    original_rows = (await db.execute(original_stmt)).all()

    members: dict[uuid.UUID, RosterMember] = {}
    for registration, student, _rc in original_rows:
        members[student.id] = RosterMember(
            student_id=student.id,
            student_number=student.student_id,
            full_name=student.full_name,
            current_semester=student.current_semester,
            registration_id=registration.id,
            is_added_via_drop=False,
        )

    # ── ADDED members ──
    # Students who joined this (section, course) slot via an approved
    # add/drop batch. One StudentScheduleAddition row per slot picked,
    # so a 3-credit course produces three rows — we dedupe per
    # student here.
    added_stmt = (
        select(Registration, Student)
        .join(
            StudentScheduleAddition,
            StudentScheduleAddition.registration_id == Registration.id,
        )
        .join(Student, Student.id == Registration.student_id)
        .where(
            StudentScheduleAddition.source_section_id == section_id,
            StudentScheduleAddition.course_id == course_id,
            Registration.status.in_(_ATTENDING_STATES),
            Registration.is_deleted == False,  # noqa: E712
            Student.is_deleted == False,  # noqa: E712
            Student.enrollment_status == EnrollmentStatus.ACTIVE,
        )
        .distinct()
    )
    added_rows = (await db.execute(added_stmt)).all()

    for registration, student in added_rows:
        # A student who is BOTH cohort-original AND has an addition
        # row pointing here is a data quirk — the original assignment
        # wins (they aren't really "added via drop"). The dict already
        # has them as ORIGINAL; skip.
        if student.id in members:
            continue
        members[student.id] = RosterMember(
            student_id=student.id,
            student_number=student.student_id,
            full_name=student.full_name,
            current_semester=student.current_semester,
            registration_id=registration.id,
            is_added_via_drop=True,
        )

    # Stable sort: originals before added, then by student_number so
    # both groups read in roll-call order.
    return sorted(
        members.values(),
        key=lambda m: (m.is_added_via_drop, m.student_number),
    )


async def section_exists(
    db: AsyncSession, section_id: uuid.UUID,
) -> bool:
    """Convenience: True if a non-deleted Section row exists for the id."""
    row = (
        await db.execute(
            select(Section.id).where(
                Section.id == section_id,
                Section.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    return row is not None
