"""
Track B (Grading) — Pydantic request/response shapes.

PR 1 surface: the two instructor reads.

  * InstructorSectionAssignmentResponse — one entry per (section,
    course) pair the calling instructor teaches in a given term.
  * SectionCourseRosterResponse        — effective roster of students
    for a (section, course) pair, with each student tagged as
    ORIGINAL (registered into the section's cohort) or ADDED (joined
    this section's slot via an approved add/drop batch).

Later PRs append breakdown / batch / agent-review / authorisation
shapes here.
"""
from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict


# ══════════════════════════════════════════════════════════════
#  Instructor — "sections I teach"
# ══════════════════════════════════════════════════════════════


class InstructorSectionAssignmentResponse(BaseModel):
    """
    One (section, course) pair the calling instructor teaches in a
    term. The grading unit is the *pair*, not just the section — a
    section serves multiple courses simultaneously and each is graded
    independently.

    Derived from the ClassScheduleSlot rows where the slot's
    ``instructor_id`` matches the caller. Deduplicated to one entry
    per (section_id, course_id).
    """
    model_config = ConfigDict(from_attributes=True)

    section_id: uuid.UUID
    section_code: str
    section_department: str
    section_semester: int
    course_id: uuid.UUID
    course_code: str
    course_title: str
    course_credit_hours: int
    term_id: uuid.UUID
    # How many distinct weekly slots the instructor has for this
    # (section, course) pair — useful for the UI to surface "you
    # teach 3 hours/week of CS101 in Section A".
    slot_count: int


# ══════════════════════════════════════════════════════════════
#  Roster — effective members of a (section, course)
# ══════════════════════════════════════════════════════════════


class RosterStudentResponse(BaseModel):
    """
    One row in the effective roster for a (section, course) pair.

    ``is_added_via_drop=True`` distinguishes students who joined this
    section through an approved add/drop batch (sourced from
    ``StudentScheduleAddition``) from students whose original cohort
    *is* this section (``Registration.section_id == section_id``).

    Students who originally registered for the course but dropped it
    (``RegistrationCourse.is_dropped == True``) and students who
    transferred *out* via add/drop to a different section are
    excluded entirely — they do not appear here at all.
    """
    model_config = ConfigDict(from_attributes=True)

    student_id: uuid.UUID
    student_number: str  # AAU "UGR/XXXX/YY" identifier
    full_name: str
    current_semester: int
    registration_id: uuid.UUID
    is_added_via_drop: bool


class SectionCourseRosterResponse(BaseModel):
    """
    Roster payload for a (section, course) pair. The summary counts
    are computed server-side so the UI doesn't have to re-tally them.
    """
    section_id: uuid.UUID
    section_code: str
    course_id: uuid.UUID
    course_code: str
    course_title: str
    term_id: uuid.UUID

    total: int
    original_count: int
    added_count: int

    students: list[RosterStudentResponse]
