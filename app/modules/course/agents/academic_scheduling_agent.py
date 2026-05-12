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
    ClassScheduleSlot, Classroom, Course, InstructorAssignment,
    Registration, ScheduleConflict, Section, Student,
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
    """
    SDS §3.1.3 + §5.3 Tables 82–84 — cohort-based, department-scoped.

    The scheduler runs **one department at a time**: an officer triggers
    scheduling for "Software Engineering" and the agent allocates +
    schedules every (semester=1..10) cohort in that department,
    drawing rooms only from that department's :class:`Classroom`
    inventory. Other departments are unaffected by the run.

    Per-department scheduling is safe because:
      - ``Classroom.department`` partitions room inventory by owner.
      - ``InstructorAssignment`` rows are department-internal in the
        seed (no cross-department teaching), so two departments
        scheduling sequentially cannot double-book an instructor.

    The agent does maintain a per-department busy map across all
    sections (room + instructor) so two cohorts within the same
    department don't clash on shared resources.
    """

    AGENT_ID_PREFIX = "AGENT_ASA_"

    def __init__(
        self,
        agent_id: Optional[str] = None,
        *,
        room_inventory: Optional[list[tuple[str, int]]] = None,
    ) -> None:
        super().__init__(agent_id=agent_id or f"{self.AGENT_ID_PREFIX}DEFAULT")
        # Test override: a hand-built list of (name, capacity) pairs
        # bypasses the Classroom DB query. Production callers pass
        # None and the agent reads the per-department inventory at
        # allocation time.
        self._room_inventory_override: Optional[list[tuple[str, int]]] = (
            sorted(room_inventory, key=lambda r: -r[1])
            if room_inventory is not None else None
        )

    # ── Classroom inventory lookup ────────────────────────────────

    async def _rooms_for_department(
        self, session: AsyncSession, department: str,
    ) -> list[tuple[str, int]]:
        """
        Returns ``[(name, capacity), …]`` for every classroom whose
        ``department`` matches, largest-first. Tests can short-circuit
        via the ``room_inventory`` constructor argument.
        """
        if self._room_inventory_override is not None:
            return self._room_inventory_override
        rows = (
            await session.execute(
                select(Classroom).where(
                    Classroom.department == department,
                    Classroom.is_deleted == False,  # noqa: E712
                ).order_by(Classroom.capacity.desc())
            )
        ).scalars().all()
        return [(c.name, c.capacity) for c in rows]

    # ── Phase 1: allocate_sections ────────────────────────────────

    async def allocate_sections(
        self,
        session: AsyncSession,
        term_id: uuid.UUID,
        department: str,
    ) -> AllocationResult:
        """
        Group REGISTERED students in this department (across every
        semester level 1–10) by ``current_semester`` and pin each to
        a cohort Section. Rooms come from ``Classroom`` filtered by
        the department.

        Idempotent on re-runs: students already pinned to a Section
        stay put; new students fill remaining capacity in existing
        sections before fresh ones are created.
        """
        result = AllocationResult()

        rooms = await self._rooms_for_department(session, department)
        if not rooms:
            # No classrooms means we can't place anyone — report all
            # affected students as failed rather than silently dropping.
            unplaced_students = (
                await session.execute(
                    select(Student.id).where(
                        Student.department == department,
                        Student.is_deleted == False,  # noqa: E712
                    )
                )
            ).scalars().all()
            for sid in unplaced_students:
                result.failed.append({
                    "student_id": str(sid),
                    "reason": f"no classrooms registered for department '{department}'",
                })
            return result

        # Pre-load existing sections in this (term, department) keyed
        # by semester so re-runs fill remaining capacity instead of
        # creating duplicates.
        existing_sections = (
            await session.execute(
                select(Section).where(
                    Section.term_id == term_id,
                    Section.department == department,
                    Section.is_deleted == False,  # noqa: E712
                )
            )
        ).scalars().all()
        sections_by_sem: dict[int, list[Section]] = defaultdict(list)
        for sec in existing_sections:
            sections_by_sem[sec.semester].append(sec)

        # Section codes are globally unique per term (across all
        # departments), so pull every code already used in the term —
        # not just this department's — to avoid collisions when other
        # departments are scheduled later.
        all_term_codes = (
            await session.execute(
                select(Section.section_code).where(
                    Section.term_id == term_id,
                    Section.is_deleted == False,  # noqa: E712
                )
            )
        ).scalars().all()
        used_codes: set[str] = set(all_term_codes)

        # Pull REGISTERED students in this department.
        rows = (
            await session.execute(
                select(Registration, Student).join(
                    Student, Registration.student_id == Student.id,
                ).where(
                    Registration.term_id == term_id,
                    Registration.status == RegistrationStatus.REGISTERED,
                    Registration.is_deleted == False,  # noqa: E712
                    Student.department == department,
                )
            )
        ).all()

        students_by_sem: dict[int, list[tuple[Registration, Student]]] = (
            defaultdict(list)
        )
        for reg, stu in rows:
            if reg.section_id is not None:
                continue   # already placed; skip
            students_by_sem[stu.current_semester].append((reg, stu))

        for sem, unplaced in students_by_sem.items():
            sections_for_sem = sections_by_sem[sem]

            # Step 1: top up existing sections.
            for sec in sections_for_sem:
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
                # Always pick the largest available room — biggest
                # cohort fits in one section rather than splitting.
                cohort_room, cohort_capacity = rooms[0]
                cohort_size = min(len(unplaced), cohort_capacity)

                section_code = _next_section_code(used_codes)
                used_codes.add(section_code)

                section = Section(
                    term_id=term_id,
                    department=department,
                    semester=sem,
                    section_code=section_code,
                    room=cohort_room,
                    capacity=cohort_capacity,
                    enrolled_count=0,
                )
                session.add(section)
                await session.flush()
                sections_for_sem.append(section)
                result.sections_created.append({
                    "section_id": str(section.id),
                    "section_code": section_code,
                    "department": department,
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
        self,
        session: AsyncSession,
        term_id: uuid.UUID,
        department: str,
    ) -> ScheduleArtefact:
        """
        Build a weekly schedule for every Section in this department
        for ``term_id``. Re-runs are idempotent: existing slots for
        the department's sections are deleted before rebuilding.

        Other departments' sections + slots are untouched.
        """
        artefact = ScheduleArtefact()

        sections = (
            await session.execute(
                select(Section).where(
                    Section.term_id == term_id,
                    Section.department == department,
                    Section.is_deleted == False,  # noqa: E712
                ).order_by(
                    Section.semester.asc(),
                    Section.section_code.asc(),
                )
            )
        ).scalars().all()
        artefact.section_count = len(sections)

        # Wipe only this department's existing slots before rebuilding.
        existing_slot_ids = (
            await session.execute(
                select(ClassScheduleSlot.id)
                .join(Section, Section.id == ClassScheduleSlot.section_id)
                .where(
                    Section.term_id == term_id,
                    Section.department == department,
                )
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

        # Per-department conflict bookkeeping — rooms + instructors
        # are department-scoped in the current model, so a sibling
        # department's slots never need to be cross-checked here.
        room_busy: dict[tuple[str, time], set[str]] = defaultdict(set)
        instructor_busy: dict[tuple[str, time], set[uuid.UUID]] = defaultdict(set)

        for sec in sections:
            # Curriculum is strictly per (department, semester) under
            # the cohort model — a CS-sem-1 cohort attends only CS-
            # tagged sem-1 courses, never SE's or BME's.
            curriculum = (
                await session.execute(
                    select(Course).where(
                        Course.semester == sec.semester,
                        Course.department == sec.department,
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
        Run both phases for a single (term, department) pair:

            1. allocate_sections — assigns every REGISTERED student
               in ``department`` to a cohort Section, drawing rooms
               from that department's :class:`Classroom` inventory.
            2. generate_schedule — builds ClassScheduleSlot rows
               sized to each course's credit_hours, scoped to this
               department's sections only.

        Other departments' sections, slots, and conflicts are
        untouched. Run this once per department per term.
        """
        session: AsyncSession = input_data["session"]
        term_id: uuid.UUID = input_data["term_id"]
        department: str = input_data["department"]

        allocation = await self.allocate_sections(session, term_id, department)
        artefact = await self.generate_schedule(session, term_id, department)

        return {
            "allocation": {
                "department": department,
                "sections_created": allocation.sections_created,
                "students_placed_count": len(allocation.students_placed),
                "students_placed": allocation.students_placed,
                "failed": allocation.failed,
            },
            "schedule": {
                "term_id": str(term_id),
                "department": department,
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
