"""
Academic Scheduling Agent — cohort-based.

Replaces the per-CourseOffering "section" model with a cohort model:

    A Section is a (term, department, semester) group of students who
    attend every course of that semester together in the same room.
    A ClassScheduleSlot pins one weekly meeting of one course inside
    that section.

The agent runs in two phases (the service composes them via
``process_task``):

  1. allocate_sections(term_id)
     - Group REGISTERED registrations by (Student.department,
       Student.current_semester).
     - For each group, split the students into cohorts whose size is
       bounded by the largest available room. Idempotent: students
       already pinned to a section are left alone; new students fill
       remaining capacity in existing sections, then spill into
       freshly-created sections. Section codes are global (A, B, C,
       … unique per term) to keep their on-screen identity simple.
     - Pin the room from a fixed inventory (SDS Table 83).

  2. generate_schedule(term_id)
     - For every Section, look up the curriculum for that semester
       (every Course where ``semester == section.semester``) and lay
       out ClassScheduleSlot rows. Each course gets exactly
       ``course.credit_hours`` hours of slots per week.
     - Slots are placed in 1-hour blocks in the standard university
       teaching window (08:30–17:30 MON–FRI). The placement is
       conflict-aware: a slot is rejected if either the section's
       room or the chosen instructor is already booked at that time
       across the whole term.
     - Anything that cannot be placed in the available window is
       recorded as a :class:`ScheduleConflict` row for the officer.

The agent owns no database session or term identity of its own —
each public method takes everything it needs as a parameter, so it
is trivially testable in isolation.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import time
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.course.agents.course_base_agent import CourseBaseAgent
from app.modules.course.models import (
    ClassScheduleSlot, Course, InstructorAssignment, Registration,
    ScheduleConflict, Section, Student,
)
from app.shared.enums import (
    RegistrationStatus, ScheduleConflictStatus, ScheduleConflictType,
)


# ── Result containers ────────────────────────────────────────────


@dataclass
class AllocationResult:
    """Outcome of :meth:`allocate_sections` for one term."""
    sections_created: list[dict[str, Any]] = field(default_factory=list)
    students_placed: list[dict[str, str]] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)


@dataclass
class ScheduleArtefact:
    """Outcome of :meth:`generate_schedule` for one term."""
    slots_created: int = 0
    section_count: int = 0
    sections: list[dict[str, Any]] = field(default_factory=list)
    conflict_ids: list[uuid.UUID] = field(default_factory=list)


# ── Agent ────────────────────────────────────────────────────────


# A teaching day is split into nine 1-hour blocks: 08:30–17:30 with a
# lunch break implicitly available because we only place classes when
# a course's credit_hours requires that many filled hours.
_DAYS = ("MON", "TUE", "WED", "THU", "FRI")
_HOUR_BLOCKS: list[tuple[time, time]] = [
    (time(8, 30),  time(9, 30)),
    (time(9, 30),  time(10, 30)),
    (time(10, 30), time(11, 30)),
    (time(11, 30), time(12, 30)),
    (time(13, 30), time(14, 30)),
    (time(14, 30), time(15, 30)),
    (time(15, 30), time(16, 30)),
    (time(16, 30), time(17, 30)),
]


class AcademicSchedulingAgent(CourseBaseAgent):
    """SDS §3.1.3 + §5.3 Tables 82–84 — cohort-based reimplementation."""

    AGENT_ID_PREFIX = "AGENT_ASA_"

    # SDS Table 83 invariant — room inventory. Capacities determine how
    # large a single cohort may grow.
    DEFAULT_ROOM_INVENTORY: list[tuple[str, int]] = [
        ("NB-101", 60), ("NB-102", 60),
        ("NB-203", 50), ("NB-204", 50),
        ("NB-305", 40),
        ("FBE-12", 80), ("FBE-14", 80),
    ]

    def __init__(
        self,
        agent_id: Optional[str] = None,
        *,
        room_inventory: Optional[list[tuple[str, int]]] = None,
    ) -> None:
        super().__init__(agent_id=agent_id or f"{self.AGENT_ID_PREFIX}DEFAULT")
        self._rooms: list[tuple[str, int]] = list(
            room_inventory or self.DEFAULT_ROOM_INVENTORY
        )
        # Sort rooms largest-first so allocation prefers fewer, fuller
        # cohorts over many small ones.
        self._rooms.sort(key=lambda r: -r[1])

    # ── Phase 1: allocate_sections ────────────────────────────────

    async def allocate_sections(
        self, session: AsyncSession, term_id: uuid.UUID,
    ) -> AllocationResult:
        """
        Group REGISTERED students for ``term_id`` by (department,
        current_semester) and pin each to a cohort Section. Idempotent
        on re-runs: a student whose Registration already has
        ``section_id`` set is skipped.
        """
        result = AllocationResult()

        # Pre-load existing sections in this term keyed by (dept, sem)
        # so re-runs can fill remaining capacity instead of creating
        # duplicates.
        existing_sections = (
            await session.execute(
                select(Section).where(
                    Section.term_id == term_id,
                    Section.is_deleted == False,  # noqa: E712
                )
            )
        ).scalars().all()
        sections_by_group: dict[tuple[str, int], list[Section]] = defaultdict(list)
        for sec in existing_sections:
            sections_by_group[(sec.department, sec.semester)].append(sec)

        # Existing global section codes (A, B, C, …) so newly-created
        # sections in this term don't collide.
        used_codes: set[str] = {sec.section_code for sec in existing_sections}

        # Pull REGISTERED students for this term and skip ones already
        # placed.
        rows = (
            await session.execute(
                select(Registration, Student).join(
                    Student, Registration.student_id == Student.id,
                ).where(
                    Registration.term_id == term_id,
                    Registration.status == RegistrationStatus.REGISTERED,
                    Registration.is_deleted == False,  # noqa: E712
                )
            )
        ).all()

        students_by_group: dict[tuple[str, int], list[tuple[Registration, Student]]] = (
            defaultdict(list)
        )
        for reg, stu in rows:
            if reg.section_id is not None:
                # Already placed — counts toward capacity in its
                # existing section but doesn't need re-placement.
                continue
            if not stu.department:
                result.failed.append({
                    "student_id": str(stu.id),
                    "reason": "student has no department recorded",
                })
                continue
            students_by_group[(stu.department, stu.current_semester)].append(
                (reg, stu),
            )

        for (dept, sem), unplaced in students_by_group.items():
            sections_for_group = sections_by_group[(dept, sem)]

            # Step 1: top up existing sections that still have capacity.
            for sec in sections_for_group:
                while unplaced and sec.enrolled_count < sec.capacity:
                    reg, stu = unplaced.pop(0)
                    reg.section_id = sec.id
                    sec.enrolled_count += 1
                    result.students_placed.append({
                        "student_id": str(stu.id),
                        "section_id": str(sec.id),
                        "section_code": sec.section_code,
                    })

            # Step 2: spill remaining students into fresh sections.
            while unplaced:
                # Pick the largest room not already double-booked at
                # this slot (room re-use across sections is fine —
                # they meet at different schedule slots).
                # Pick the largest single room available; cohort size
                # is capped at that room's capacity.
                cohort_capacity = max(cap for _, cap in self._rooms)
                cohort_room = next(
                    name for name, cap in self._rooms if cap == cohort_capacity
                )
                cohort_size = min(len(unplaced), cohort_capacity)

                section_code = _next_section_code(used_codes)
                used_codes.add(section_code)

                section = Section(
                    term_id=term_id,
                    department=dept,
                    semester=sem,
                    section_code=section_code,
                    room=cohort_room,
                    capacity=cohort_capacity,
                    enrolled_count=0,
                )
                session.add(section)
                await session.flush()
                sections_for_group.append(section)
                result.sections_created.append({
                    "section_id": str(section.id),
                    "section_code": section_code,
                    "department": dept,
                    "semester": sem,
                    "room": cohort_room,
                    "capacity": cohort_capacity,
                })

                for _ in range(cohort_size):
                    reg, stu = unplaced.pop(0)
                    reg.section_id = section.id
                    section.enrolled_count += 1
                    result.students_placed.append({
                        "student_id": str(stu.id),
                        "section_id": str(section.id),
                        "section_code": section.section_code,
                    })

        await session.flush()
        return result

    # ── Phase 2: generate_schedule ────────────────────────────────

    async def generate_schedule(
        self, session: AsyncSession, term_id: uuid.UUID,
    ) -> ScheduleArtefact:
        """
        Build a weekly schedule for every Section in ``term_id``.

        For each section:
            curriculum = courses where Course.semester == section.semester
            for each course:
                place ``course.credit_hours`` hour-blocks,
                avoiding (room, slot) and (instructor, slot) conflicts
                that already exist in this term.

        Re-runs are idempotent: existing slots are dropped before
        rebuilding for the section so the schedule reflects the
        latest student placements.
        """
        artefact = ScheduleArtefact()

        sections = (
            await session.execute(
                select(Section).where(
                    Section.term_id == term_id,
                    Section.is_deleted == False,  # noqa: E712
                ).order_by(Section.section_code.asc())
            )
        ).scalars().all()
        artefact.section_count = len(sections)

        # Wipe any existing slots for the term so we rebuild cleanly.
        existing_slot_ids = (
            await session.execute(
                select(ClassScheduleSlot.id)
                .join(Section, Section.id == ClassScheduleSlot.section_id)
                .where(Section.term_id == term_id)
            )
        ).scalars().all()
        if existing_slot_ids:
            for slot in (
                await session.execute(
                    select(ClassScheduleSlot).where(
                        ClassScheduleSlot.id.in_(existing_slot_ids),
                    )
                )
            ).scalars().all():
                await session.delete(slot)
            await session.flush()

        # Term-wide conflict bookkeeping. Each entry is a (day, start)
        # tuple → set of room names / instructor ids in use.
        room_busy: dict[tuple[str, time], set[str]] = defaultdict(set)
        instructor_busy: dict[tuple[str, time], set[uuid.UUID]] = defaultdict(set)

        for sec in sections:
            curriculum = (
                await session.execute(
                    select(Course).where(
                        Course.semester == sec.semester,
                        Course.is_deleted == False,  # noqa: E712
                    ).order_by(Course.code.asc())
                )
            ).scalars().all()

            sec_slots: list[dict[str, Any]] = []

            for course in curriculum:
                instructor_id = await self._pick_instructor(
                    session, course_id=course.id, term_id=term_id,
                )

                placed_count = 0
                for day in _DAYS:
                    if placed_count == course.credit_hours:
                        break
                    for start, end in _HOUR_BLOCKS:
                        if placed_count == course.credit_hours:
                            break
                        # Room collision: different section, same room,
                        # same slot.
                        if sec.room and sec.room in room_busy[(day, start)]:
                            continue
                        # Instructor collision: same instructor already
                        # teaching another section/course at this slot.
                        if (
                            instructor_id
                            and instructor_id in instructor_busy[(day, start)]
                        ):
                            continue
                        slot = ClassScheduleSlot(
                            section_id=sec.id,
                            course_id=course.id,
                            instructor_id=instructor_id,
                            day_of_week=day,
                            start_time=start,
                            end_time=end,
                        )
                        session.add(slot)
                        sec_slots.append({
                            "course_code": course.code,
                            "course_title": course.title,
                            "day_of_week": day,
                            "start_time": start.isoformat(timespec="minutes"),
                            "end_time": end.isoformat(timespec="minutes"),
                            "instructor_id": (
                                str(instructor_id) if instructor_id else None
                            ),
                        })
                        if sec.room:
                            room_busy[(day, start)].add(sec.room)
                        if instructor_id:
                            instructor_busy[(day, start)].add(instructor_id)
                        placed_count += 1
                        artefact.slots_created += 1

                if placed_count < course.credit_hours:
                    conflict = ScheduleConflict(
                        term_id=term_id,
                        department=sec.department,
                        conflict_type=ScheduleConflictType.ROOM_DOUBLE_BOOKED,
                        section_id=sec.id,
                        instructor_id=instructor_id,
                        time_slot=None,
                        room=sec.room,
                        description=(
                            f"Could not place all {course.credit_hours} "
                            f"weekly hours for {course.code} in section "
                            f"{sec.section_code}: only {placed_count} "
                            "block(s) fit before the teaching window or "
                            "instructor availability ran out."
                        ),
                        detected_by_agent_id=self.agent_id,
                        status=ScheduleConflictStatus.OPEN,
                    )
                    session.add(conflict)
                    await session.flush()
                    artefact.conflict_ids.append(conflict.id)

            artefact.sections.append({
                "section_id": str(sec.id),
                "section_code": sec.section_code,
                "department": sec.department,
                "semester": sec.semester,
                "room": sec.room,
                "capacity": sec.capacity,
                "enrolled_count": sec.enrolled_count,
                "slots": sec_slots,
            })

        await session.flush()
        return artefact

    # ── Helpers ───────────────────────────────────────────────────

    async def _pick_instructor(
        self,
        session: AsyncSession,
        *,
        course_id: uuid.UUID,
        term_id: uuid.UUID,
    ) -> Optional[uuid.UUID]:
        """Pick the first InstructorAssignment for (course, term)."""
        row = (
            await session.execute(
                select(InstructorAssignment).where(
                    InstructorAssignment.course_id == course_id,
                    InstructorAssignment.term_id == term_id,
                ).order_by(InstructorAssignment.created_at.asc())
            )
        ).scalars().first()
        return row.instructor_id if row else None

    # ── BaseAgent contract ────────────────────────────────────────

    async def process_task(
        self, input_data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Run both phases for a term:

            1. allocate_sections — assigns every REGISTERED student
               in the term to a Section.
            2. generate_schedule — builds ClassScheduleSlot rows
               sized to each course's credit_hours.
        """
        session: AsyncSession = input_data["session"]
        term_id: uuid.UUID = input_data["term_id"]

        allocation = await self.allocate_sections(session, term_id)
        artefact = await self.generate_schedule(session, term_id)

        return {
            "allocation": {
                "sections_created": allocation.sections_created,
                "students_placed_count": len(allocation.students_placed),
                "students_placed": allocation.students_placed,
                "failed": allocation.failed,
            },
            "schedule": {
                "term_id": str(term_id),
                "section_count": artefact.section_count,
                "slots_created": artefact.slots_created,
                "sections": artefact.sections,
                "conflict_count": len(artefact.conflict_ids),
                "conflict_ids": [str(cid) for cid in artefact.conflict_ids],
            },
        }


def _next_section_code(used: set[str]) -> str:
    """A, B, ..., Z, AA, AB, ..."""
    n = 1
    while True:
        # Convert n to a base-26 string using A-Z digits.
        x = n
        chars: list[str] = []
        while x:
            x, r = divmod(x - 1, 26)
            chars.append(chr(ord("A") + r))
        candidate = "".join(reversed(chars))
        if candidate not in used:
            return candidate
        n += 1
