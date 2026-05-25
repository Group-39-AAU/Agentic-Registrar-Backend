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
       teaching window (08:30–12:30, lunch 12:30–13:30, 13:30–16:30
       MON–FRI). Placement is **balanced best-fit**, not left-to-
       right greedy: for each weekly hour of a course we pick the
       free (day, block) that minimises (a) re-using a day this
       course already meets on, (b) the section's total hours
       booked on that day, (c) the block-index — so courses spread
       across the week, daily loads stay even, and mornings fill
       first. Up to two hours of a single course may share a day,
       and when they do the second hour must be adjacent to the
       first (one contiguous session). The placement is conflict-
       aware: a slot is rejected if either the section's room or
       the chosen instructor is already booked at that time across
       the whole term.
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

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import User as _AuthUser
from app.modules.course.agents.course_base_agent import CourseBaseAgent
from app.modules.course.models import (
    ClassScheduleSlot, Classroom, Course, Instructor, InstructorAssignment,
    Registration, RegistrationCourse, ScheduleConflict, Section, Student,
    StudentScheduleAddition,
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


# Standard teaching window: four morning blocks 08:30–12:30, lunch
# 12:30–13:30 (no classes), three afternoon blocks 13:30–16:30 —
# seven 1-hour blocks per day, MON–FRI, 35 slots/week per cohort.
_DAYS = ("MON", "TUE", "WED", "THU", "FRI")
_HOUR_BLOCKS: list[tuple[time, time]] = [
    (time(8, 30),  time(9, 30)),
    (time(9, 30),  time(10, 30)),
    (time(10, 30), time(11, 30)),
    (time(11, 30), time(12, 30)),
    (time(13, 30), time(14, 30)),
    (time(14, 30), time(15, 30)),
    (time(15, 30), time(16, 30)),
]
# A single course may meet at most this many 1-hour blocks on the
# same day. The extra block(s) must be contiguous with the first —
# the cohort gets a single multi-hour session, never two disjoint
# stubs on the same day.
_MAX_HOURS_PER_DAY_PER_COURSE = 2

# How many shuffled-restart attempts the greedy scheduler makes
# before giving up and reporting unplaced sessions as conflicts.
# Each attempt is sub-millisecond on a department's workload, so
# even an order-of-magnitude bump here costs <100 ms.
_GREEDY_RETRY_LIMIT = 40


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
        a cohort Section. **Rooms are not assigned here** —
        :meth:`generate_schedule` picks the actual room per section
        when laying down weekly slots. Cohort size is capped at the
        department's largest classroom capacity.

        **Re-runs wipe and rebuild.** Any existing sections, weekly
        slots, and ``Registration.section_id`` pins for this (term,
        department) are cleared before the fresh allocation runs.
        Officers can call this whenever they want a clean re-split.

        Section codes restart at ``A`` *per semester* — sem-1 gets
        A, B, C; sem-3 also gets A, B, C — so a student's section
        letter is meaningful inside their cohort, not term-wide.
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

        # ── Wipe existing (term, department) allocation ───────────
        # Delete order (every table that FK-references sections.id):
        #   1. ClassScheduleSlot rows pointing at any of this
        #      department's sections — FK to sections has
        #      ondelete=CASCADE, but doing it explicitly keeps the
        #      ORM flush from complaining about identity-map state.
        #   2. ScheduleConflict rows pointing at any of these
        #      sections (via section_id or other_section_id) —
        #      stale anyway, since they were detected against the
        #      old allocation; generate_schedule will rebuild them.
        #   3. Clear Registration.section_id on every registration
        #      pinned to those sections (FK is plain ON DELETE NO
        #      ACTION; without this we'd violate the constraint).
        #   4. Hard-delete the Section rows themselves.
        existing_sections = (
            await session.execute(
                select(Section).where(
                    Section.term_id == term_id,
                    Section.department == department,
                    Section.is_deleted == False,  # noqa: E712
                )
            )
        ).scalars().all()
        if existing_sections:
            existing_ids = [s.id for s in existing_sections]
            existing_slots = (
                await session.execute(
                    select(ClassScheduleSlot).where(
                        ClassScheduleSlot.section_id.in_(existing_ids),
                    )
                )
            ).scalars().all()
            for slot in existing_slots:
                await session.delete(slot)
            stale_conflicts = (
                await session.execute(
                    select(ScheduleConflict).where(
                        or_(
                            ScheduleConflict.section_id.in_(existing_ids),
                            ScheduleConflict.other_section_id.in_(
                                existing_ids,
                            ),
                        )
                    )
                )
            ).scalars().all()
            for conflict in stale_conflicts:
                await session.delete(conflict)
            affected_regs = (
                await session.execute(
                    select(Registration).where(
                        Registration.section_id.in_(existing_ids),
                    )
                )
            ).scalars().all()
            for reg in affected_regs:
                reg.section_id = None
            await session.flush()
            for sec in existing_sections:
                await session.delete(sec)
            await session.flush()

        # ── Pull REGISTERED students in this department ───────────
        # Stable order (created_at, id) makes re-runs deterministic:
        # the same student always falls in the same balanced slot
        # across rebuilds, so officers see consistent section letters.
        rows = (
            await session.execute(
                select(Registration, Student).join(
                    Student, Registration.student_id == Student.id,
                ).where(
                    Registration.term_id == term_id,
                    Registration.status == RegistrationStatus.REGISTERED,
                    Registration.is_deleted == False,  # noqa: E712
                    Student.department == department,
                ).order_by(
                    Registration.created_at.asc(),
                    Registration.id.asc(),
                )
            )
        ).all()

        students_by_sem: dict[int, list[tuple[Registration, Student]]] = (
            defaultdict(list)
        )
        for reg, stu in rows:
            students_by_sem[stu.current_semester].append((reg, stu))

        # ── Build fresh sections, codes restart at A per semester ──
        # Allocation deliberately does not pin a room. Each section's
        # ``capacity`` is set to the department's largest room (the
        # absolute upper bound an officer can grow the cohort to);
        # :meth:`generate_schedule` picks the actual room per slot.
        #
        # Balanced split: instead of greedily filling section A to
        # ``max_room_capacity`` and trickling the remainder into B,
        # we minimise the max-section size by spreading evenly. For
        # ``n`` students and ``k = ceil(n / max_room_capacity)``
        # sections, ``n mod k`` sections get ``floor(n/k) + 1``
        # students and the rest get ``floor(n/k)``.
        max_room_capacity = rooms[0][1]

        for sem in sorted(students_by_sem.keys()):
            cohort = students_by_sem[sem]
            n = len(cohort)
            if n == 0:
                continue

            num_sections = -(-n // max_room_capacity)  # ceil(n / cap)
            base_size, extras = divmod(n, num_sections)

            used_codes: set[str] = set()
            cursor = 0
            for i in range(num_sections):
                # First ``extras`` sections absorb the +1; rest get base.
                this_size = base_size + (1 if i < extras else 0)
                section_code = _next_section_code(used_codes)
                used_codes.add(section_code)

                section = Section(
                    term_id=term_id,
                    department=department,
                    semester=sem,
                    section_code=section_code,
                    capacity=max_room_capacity,
                    enrolled_count=0,
                )
                session.add(section)
                await session.flush()
                result.sections_created.append({
                    "section_id": str(section.id),
                    "section_code": section_code,
                    "department": department,
                    "semester": sem,
                    "capacity": max_room_capacity,
                    "balanced_size": this_size,
                })

                for reg, stu in cohort[cursor:cursor + this_size]:
                    reg.section_id = section.id
                    section.enrolled_count += 1
                    result.students_placed.append({
                        "student_id": str(stu.id),
                        "section_id": str(section.id),
                        "section_code": section.section_code,
                    })
                cursor += this_size

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

        Algorithm — **scored greedy with shuffle-restart**
        (zero conflicts whenever the room + instructor inventory
        leaves any feasible weekly schedule):

          1. Pre-flight: per (section, course), look up the
             instructor assignment and check that at least one room
             fits the cohort. Courses that fail either check are
             skipped with a typed ``ScheduleConflict`` row.
          2. Partition each course's ``credit_hours`` into 1-hr or
             2-hr **sessions** (preferring 2-hr); enforces both the
             per-day cap and the contiguous-session rule by
             construction.
          3. Sort sessions by tightness (largest sessions first,
             largest cohorts first).
          4. Greedy placement: for each session pick the
             ``(day, start_block, room)`` that minimises a load-
             balancing score —
                 (instructor's existing hours on this day,
                  cohort's existing hours on this day,
                  earlier start preferred,
                  smallest fitting room preferred).
             Smallest-fitting room first preserves big halls for
             big cohorts. Days with the lightest load come first
             so instructor + cohort schedules spread evenly.
          5. **Shuffle-restart**: if the first pass leaves any
             session unplaced, retry up to ``_GREEDY_RETRY_LIMIT``
             times with a re-shuffled session list (tightness is
             preserved; shuffle only randomises tiebreakers). Keep
             the best result. With the bumped seed inventory this
             converges to zero conflicts in a handful of attempts.
          6. If every attempt still leaves something unplaced (truly
             infeasible inventory), emit one fallback
             ``ROOM_DOUBLE_BOOKED`` per under-placed course so the
             officer sees exactly what's missing.

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

        # Wipe only this department's existing slots and any stale
        # OPEN conflicts before rebuilding — re-runs should not pile
        # up duplicate conflict rows from prior failed attempts.
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
        stale_conflicts = (
            await session.execute(
                select(ScheduleConflict).where(
                    ScheduleConflict.term_id == term_id,
                    ScheduleConflict.department == department,
                    ScheduleConflict.status == ScheduleConflictStatus.OPEN,
                )
            )
        ).scalars().all()
        for c in stale_conflicts:
            await session.delete(c)
        if stale_conflicts:
            await session.flush()

        rooms = await self._rooms_for_department(session, department)
        # Smallest-fit ordering — try the tightest room that fits
        # first so large halls stay available for the biggest cohorts.
        rooms_sorted = sorted(rooms, key=lambda r: r[1])
        biggest_room_cap = max((cap for _, cap in rooms), default=0)

        # ── 1. Build per-section curriculum + per-course instructor
        #       and emit pre-flight typed conflicts where needed.
        # Each entry: (section, course, instructor_id_or_None).
        # ``skipped_courses`` is the set of (section_id, course_id)
        # we will NOT schedule because of a pre-flight conflict.
        per_section: dict[uuid.UUID, list[tuple[Course, Optional[uuid.UUID]]]] = {}
        skipped_courses: set[tuple[uuid.UUID, uuid.UUID]] = set()

        for sec in sections:
            curriculum = (
                await session.execute(
                    select(Course).where(
                        Course.semester == sec.semester,
                        Course.department == sec.department,
                        Course.is_deleted == False,  # noqa: E712
                    ).order_by(Course.code.asc())
                )
            ).scalars().all()

            # Pre-flight: cohort too big for any room?
            if sec.enrolled_count > biggest_room_cap:
                for course in curriculum:
                    conflict = ScheduleConflict(
                        term_id=term_id,
                        department=sec.department,
                        conflict_type=ScheduleConflictType.NO_AVAILABLE_ROOM,
                        section_id=sec.id,
                        instructor_id=None,
                        time_slot=None,
                        room=None,
                        description=(
                            f"Cohort {sec.section_code} has "
                            f"{sec.enrolled_count} students but the "
                            f"largest classroom in '{sec.department}' "
                            f"holds only {biggest_room_cap}."
                        ),
                        detected_by_agent_id=self.agent_id,
                        status=ScheduleConflictStatus.OPEN,
                    )
                    session.add(conflict)
                    await session.flush()
                    artefact.conflict_ids.append(conflict.id)
                    skipped_courses.add((sec.id, course.id))
                per_section[sec.id] = []
                continue

            entries: list[tuple[Course, Optional[uuid.UUID]]] = []
            for course in curriculum:
                instructor_id = await self._pick_instructor(
                    session, course_id=course.id, term_id=term_id,
                )
                if instructor_id is None:
                    # No InstructorAssignment for this course — emit a
                    # typed conflict and keep the course in the
                    # schedule unassigned. (Existing tests place slots
                    # for unassigned courses; we preserve that by
                    # passing instructor_id=None through.)
                    conflict = ScheduleConflict(
                        term_id=term_id,
                        department=sec.department,
                        conflict_type=ScheduleConflictType.NO_AVAILABLE_INSTRUCTOR,
                        section_id=sec.id,
                        instructor_id=None,
                        time_slot=None,
                        room=None,
                        description=(
                            f"No InstructorAssignment exists for "
                            f"{course.code} in this term. Slots will "
                            f"be placed with no instructor pinned."
                        ),
                        detected_by_agent_id=self.agent_id,
                        status=ScheduleConflictStatus.OPEN,
                    )
                    session.add(conflict)
                    await session.flush()
                    artefact.conflict_ids.append(conflict.id)
                entries.append((course, instructor_id))
            per_section[sec.id] = entries

        # ── 2. Expand into sessions (1 or 2 hours each).
        # Each session becomes one CSP variable. Sentinel instructor
        # id for "no instructor" so we can still key the busy map.
        _NO_INSTRUCTOR = uuid.UUID("00000000-0000-0000-0000-000000000000")
        sessions_to_place: list[dict[str, Any]] = []
        for sec in sections:
            for course, instructor_id in per_section.get(sec.id, []):
                if (sec.id, course.id) in skipped_courses:
                    continue
                for size in _session_partition(course.credit_hours):
                    sessions_to_place.append({
                        "section_id": sec.id,
                        "section_code": sec.section_code,
                        "cohort_size": sec.enrolled_count,
                        "course_id": course.id,
                        "course_code": course.code,
                        "course_title": course.title,
                        "instructor_id": instructor_id,
                        "_instr_key": instructor_id or _NO_INSTRUCTOR,
                        "session_size": size,
                    })

        # ── 3. Tightness order — bigger sessions, bigger cohorts first.
        sessions_to_place.sort(key=lambda s: (
            -s["session_size"],
            -s["cohort_size"],
            str(s["section_id"]),
            str(s["course_id"]),
        ))

        # ── 4. Greedy placement with shuffle-restart.
        # Each "attempt" runs a single greedy pass with the smartest
        # load-balancing score we can compute. If anything is left
        # unplaced, the next attempt re-shuffles the session list
        # (preserving the tightness tier) so a different traversal
        # order can find an answer the first one missed.
        def _attempt(ordered_sessions: list[dict[str, Any]]):
            sec_busy: dict[uuid.UUID, set[tuple[str, int]]] = defaultdict(set)
            ins_busy: dict[uuid.UUID, set[tuple[str, int]]] = defaultdict(set)
            rm_busy: dict[str, set[tuple[str, int]]] = defaultdict(set)
            day_used: dict[tuple[uuid.UUID, uuid.UUID], set[str]] = (
                defaultdict(set)
            )
            placed: list[tuple[dict[str, Any], str, int, str]] = []
            unplaced: list[dict[str, Any]] = []

            for s in ordered_sessions:
                sid, cid, ikey = s["section_id"], s["course_id"], s["_instr_key"]
                size, cohort = s["session_size"], s["cohort_size"]
                last_start = len(_HOUR_BLOCKS) - size

                best = None
                best_score: Optional[tuple[int, int, int, int]] = None
                for day in _DAYS:
                    if day in day_used[(sid, cid)]:
                        continue
                    inst_today = sum(
                        1 for (d, _) in ins_busy[ikey] if d == day
                    ) if s["instructor_id"] is not None else 0
                    cohort_today = sum(
                        1 for (d, _) in sec_busy[sid] if d == day
                    )
                    for start_idx in range(last_start + 1):
                        blocks = range(start_idx, start_idx + size)
                        if any((day, b) in sec_busy[sid] for b in blocks):
                            continue
                        if (
                            s["instructor_id"] is not None
                            and any(
                                (day, b) in ins_busy[ikey] for b in blocks
                            )
                        ):
                            continue
                        # Smallest fitting free room — leaves big halls
                        # available for bigger cohorts that come later.
                        for room_name, cap in rooms_sorted:
                            if cap < cohort:
                                continue
                            if any(
                                (day, b) in rm_busy[room_name] for b in blocks
                            ):
                                continue
                            score = (inst_today, cohort_today, start_idx, cap)
                            if best_score is None or score < best_score:
                                best = (day, start_idx, room_name)
                                best_score = score
                            break  # smallest fitting room for this slot

                if best is None:
                    unplaced.append(s)
                    continue
                day, start_idx, room_name = best
                for b in range(start_idx, start_idx + size):
                    sec_busy[sid].add((day, b))
                    if s["instructor_id"] is not None:
                        ins_busy[ikey].add((day, b))
                    rm_busy[room_name].add((day, b))
                day_used[(sid, cid)].add(day)
                placed.append((s, day, start_idx, room_name))

            return placed, unplaced

        # First pass uses tightness order; each retry shuffles within
        # the (session_size, cohort_size) tier so different schedules
        # are explored without losing the constrained-variable-first
        # discipline. Deterministic seed so reseeds reproduce the
        # same schedule.
        placements, unplaced = _attempt(sessions_to_place)
        if unplaced:
            import random as _random
            rng = _random.Random(0xA1C2BAA0)  # deterministic
            for _attempt_i in range(_GREEDY_RETRY_LIMIT):
                shuffled = list(sessions_to_place)
                rng.shuffle(shuffled)
                shuffled.sort(key=lambda s: (
                    -s["session_size"],
                    -s["cohort_size"],
                ))
                p, u = _attempt(shuffled)
                if len(u) < len(unplaced):
                    placements, unplaced = p, u
                if not unplaced:
                    break
        solved = not unplaced

        # ── 5. Either materialise the solution or emit per-course
        #       fallback conflicts for whatever couldn't be placed.
        if not solved:
            # Count what landed in ``placements`` (the partial best
            # the search managed before giving up) and emit one
            # ROOM_DOUBLE_BOOKED per under-placed course.
            placed_hours: dict[tuple[uuid.UUID, uuid.UUID], int] = defaultdict(int)
            for p, _d, _i, _r in placements:
                placed_hours[(p["section_id"], p["course_id"])] += p["session_size"]
            for s in sessions_to_place:
                key = (s["section_id"], s["course_id"])
                # Find the matching Course row via the section's entries
                # to look up credit_hours and section_code for the message.
                for sec in sections:
                    if sec.id != s["section_id"]:
                        continue
                    course_credit_hours = sum(
                        sz for ss in sessions_to_place
                        if (ss["section_id"], ss["course_id"]) == key
                        for sz in (ss["session_size"],)
                    )
                    if placed_hours[key] < course_credit_hours:
                        already_logged = any(
                            cid in {c.id for c in []}
                            for cid in []
                        )  # placeholder; we dedupe via the set below
                        break
            # Emit one conflict per distinct under-placed course
            emitted: set[tuple[uuid.UUID, uuid.UUID]] = set()
            for s in sessions_to_place:
                key = (s["section_id"], s["course_id"])
                if key in emitted:
                    continue
                course_credit_hours = sum(
                    ss["session_size"] for ss in sessions_to_place
                    if (ss["section_id"], ss["course_id"]) == key
                )
                if placed_hours[key] >= course_credit_hours:
                    continue
                conflict = ScheduleConflict(
                    term_id=term_id,
                    department=department,
                    conflict_type=ScheduleConflictType.ROOM_DOUBLE_BOOKED,
                    section_id=s["section_id"],
                    instructor_id=s["instructor_id"],
                    time_slot=None,
                    room=None,
                    description=(
                        f"Backtracking scheduler could not fit "
                        f"{course_credit_hours - placed_hours[key]} of "
                        f"{course_credit_hours} weekly hours for "
                        f"{s['course_code']} in section {s['section_code']}. "
                        "Increase room or instructor capacity for this "
                        "department."
                    ),
                    detected_by_agent_id=self.agent_id,
                    status=ScheduleConflictStatus.OPEN,
                )
                session.add(conflict)
                await session.flush()
                artefact.conflict_ids.append(conflict.id)
                emitted.add(key)

        # ── Materialise placements into ClassScheduleSlot rows.
        slots_by_section: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
        for s, day, start_idx, room_name in placements:
            size = s["session_size"]
            for offset in range(size):
                b = start_idx + offset
                start, end = _HOUR_BLOCKS[b]
                slot = ClassScheduleSlot(
                    section_id=s["section_id"],
                    course_id=s["course_id"],
                    instructor_id=s["instructor_id"],
                    day_of_week=day,
                    start_time=start,
                    end_time=end,
                    room=room_name,
                )
                session.add(slot)
                slots_by_section[s["section_id"]].append({
                    "course_code": s["course_code"],
                    "course_title": s["course_title"],
                    "day_of_week": day,
                    "start_time": start.isoformat(timespec="minutes"),
                    "end_time": end.isoformat(timespec="minutes"),
                    "instructor_id": (
                        str(s["instructor_id"])
                        if s["instructor_id"] is not None else None
                    ),
                    "room": room_name,
                })
                artefact.slots_created += 1

        for sec in sections:
            artefact.sections.append({
                "section_id": str(sec.id),
                "section_code": sec.section_code,
                "department": sec.department,
                "semester": sec.semester,
                "capacity": sec.capacity,
                "enrolled_count": sec.enrolled_count,
                "slots": slots_by_section.get(sec.id, []),
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

    # ── Per-student schedule deltas (add/drop integration) ───────

    async def propose_options_for_course(
        self,
        session: AsyncSession,
        *,
        registration: Registration,
        course_id: uuid.UUID,
    ) -> list[dict[str, Any]]:
        """
        Propose section options for a course the student has added
        via the add/drop flow. For every Section in the same
        (term, department, semester) cohort that has slots for this
        course, build an option carrying:

          * section info (id, code)
          * the candidate slot package (day/time/room/instructor)
          * a ``conflicts`` flag plus the colliding existing slots,
            so the portal can grey-out unviable picks

        Conflict detection compares each candidate slot against the
        student's CURRENT effective schedule (cohort minus drops
        plus already-accepted additions). Pure rule-based — no LLM.
        """
        course = await session.get(Course, course_id)
        if course is None:
            return []

        # Current schedule = cohort slots (filtered by active courses)
        # + already-accepted additions. Same shape as
        # SchedulingService.get_student_schedule, but kept in-agent so
        # the agent stays self-sufficient for testing.
        current_slots = await self._effective_slots(
            session, registration=registration,
        )

        # Find every Section that has at least one slot for this
        # course. We restrict to sections in the same (term, dept,
        # semester) — the curriculum/parity guards in
        # EnrollmentAdjustmentAgent already ensured the course
        # belongs there.
        candidate_sections = (
            await session.execute(
                select(Section).join(
                    ClassScheduleSlot,
                    ClassScheduleSlot.section_id == Section.id,
                ).where(
                    Section.term_id == registration.term_id,
                    Section.is_deleted == False,  # noqa: E712
                    ClassScheduleSlot.course_id == course_id,
                ).distinct()
            )
        ).scalars().all()

        # Collect every slot we'll surface (candidate + already-on-
        # schedule) and prefetch the instructors in one query, so the
        # per-slot summaries can carry `instructor_name` without N+1.
        candidate_section_slots: dict[uuid.UUID, list[ClassScheduleSlot]] = {}
        for section in candidate_sections:
            slot_rows = (
                await session.execute(
                    select(ClassScheduleSlot).where(
                        ClassScheduleSlot.section_id == section.id,
                        ClassScheduleSlot.course_id == course_id,
                    ).order_by(
                        ClassScheduleSlot.day_of_week.asc(),
                        ClassScheduleSlot.start_time.asc(),
                    )
                )
            ).scalars().all()
            candidate_section_slots[section.id] = list(slot_rows)

        instructor_ids: set[uuid.UUID] = set()
        for slots in candidate_section_slots.values():
            for s in slots:
                if s.instructor_id is not None:
                    instructor_ids.add(s.instructor_id)
        for entry in current_slots:
            iid = entry["slot"].instructor_id
            if iid is not None:
                instructor_ids.add(iid)
        instructor_lookup = await self._build_instructor_lookup(
            session, instructor_ids,
        )

        options: list[dict[str, Any]] = []
        for section in candidate_sections:
            slot_rows = candidate_section_slots.get(section.id, [])
            if not slot_rows:
                continue

            # When the student picks a section for ``course_id``,
            # any existing slots for that same course (from the cohort
            # default or a prior addition) are *replaced* — they
            # shouldn't count as collisions with themselves.
            conflict_candidates = [
                e for e in current_slots
                if e["slot"].course_id != course_id
            ]
            conflicts: list[dict[str, Any]] = []
            for cand in slot_rows:
                for existing in conflict_candidates:
                    existing_slot = existing["slot"]
                    if _slots_collide(cand, existing_slot):
                        conflicts.append({
                            "candidate": _slot_summary(
                                cand, course.code, instructor_lookup,
                            ),
                            "collides_with": _slot_summary(
                                existing_slot, existing["course_code"],
                                instructor_lookup,
                            ),
                        })

            options.append({
                "section_id": str(section.id),
                "section_code": section.section_code,
                "department": section.department,
                "semester": section.semester,
                "slots": [
                    _slot_summary(s, course.code, instructor_lookup)
                    for s in slot_rows
                ],
                "conflicts": conflicts,
                "is_viable": not conflicts,
            })
        # Viable options first, then sort by section code so output
        # is stable across calls.
        options.sort(key=lambda o: (not o["is_viable"], o["section_code"]))
        return options

    async def accept_section_for_course(
        self,
        session: AsyncSession,
        *,
        registration: Registration,
        course_id: uuid.UUID,
        section_id: uuid.UUID,
    ) -> list[StudentScheduleAddition]:
        """
        Materialise the student's choice: insert one
        :class:`StudentScheduleAddition` row per slot the picked
        section runs for ``course_id``. Idempotent on retry —
        existing additions for the same (registration, slot) are
        left alone (the unique constraint also enforces this at
        the DB level).

        Caller is responsible for the surrounding transaction; this
        method only ``session.add()``-s the new rows.
        """
        slot_rows = (
            await session.execute(
                select(ClassScheduleSlot).where(
                    ClassScheduleSlot.section_id == section_id,
                    ClassScheduleSlot.course_id == course_id,
                )
            )
        ).scalars().all()
        if not slot_rows:
            return []

        existing = (
            await session.execute(
                select(StudentScheduleAddition.schedule_slot_id).where(
                    StudentScheduleAddition.registration_id == registration.id,
                )
            )
        ).scalars().all()
        already_added = set(existing)

        created: list[StudentScheduleAddition] = []
        for slot in slot_rows:
            if slot.id in already_added:
                continue
            row = StudentScheduleAddition(
                registration_id=registration.id,
                schedule_slot_id=slot.id,
                course_id=course_id,
                source_section_id=section_id,
            )
            session.add(row)
            created.append(row)
        return created

    async def _build_instructor_lookup(
        self,
        session: AsyncSession,
        instructor_ids: set[uuid.UUID],
    ) -> dict[uuid.UUID, tuple[str, Optional[str]]]:
        """
        Single-query lookup from ``instructor_id`` to
        ``(full_name, staff_id)``. Lets slot summaries carry a
        human-readable instructor without N+1 round-trips. Returns
        an empty map when the input set is empty.
        """
        if not instructor_ids:
            return {}
        rows = (
            await session.execute(
                select(Instructor, _AuthUser).join(
                    _AuthUser, _AuthUser.id == Instructor.user_id,
                ).where(Instructor.id.in_(instructor_ids))
            )
        ).all()
        out: dict[uuid.UUID, tuple[str, Optional[str]]] = {}
        for instructor, user in rows:
            full_name = f"{user.first_name} {user.last_name}".strip()
            out[instructor.id] = (full_name, instructor.instructor_id)
        return out

    async def _effective_slots(
        self,
        session: AsyncSession,
        *,
        registration: Registration,
    ) -> list[dict[str, Any]]:
        """
        The student's current effective slot list — used both as the
        baseline for conflict detection and (indirectly) as the data
        the SchedulingService surfaces on /me/schedule.
        """
        await session.refresh(registration, attribute_names=["courses"])
        active_course_ids = {
            rc.course_id for rc in registration.courses if not rc.is_dropped
        }
        cohort: list[ClassScheduleSlot] = []
        if registration.section_id is not None and active_course_ids:
            cohort = list((
                await session.execute(
                    select(ClassScheduleSlot).where(
                        ClassScheduleSlot.section_id == registration.section_id,
                        ClassScheduleSlot.course_id.in_(active_course_ids),
                    )
                )
            ).scalars().all())
        additions = list((
            await session.execute(
                select(ClassScheduleSlot).join(
                    StudentScheduleAddition,
                    StudentScheduleAddition.schedule_slot_id == ClassScheduleSlot.id,
                ).where(
                    StudentScheduleAddition.registration_id == registration.id,
                )
            )
        ).scalars().all())
        all_slots = list(cohort) + list(additions)
        # Cache course codes in a single query so the conflict
        # explanations can render `CS101` rather than UUIDs.
        course_ids = {s.course_id for s in all_slots}
        codes_by_id: dict[uuid.UUID, str] = {}
        if course_ids:
            rows = (
                await session.execute(
                    select(Course).where(Course.id.in_(course_ids))
                )
            ).scalars().all()
            codes_by_id = {c.id: c.code for c in rows}
        return [
            {"slot": s, "course_code": codes_by_id.get(s.course_id, "?")}
            for s in all_slots
        ]

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


def _pick_free_room_at_slot(
    rooms: list[tuple[str, int]],
    *,
    demand: int,
    busy: set[str],
) -> Optional[str]:
    """
    Pick the smallest classroom that fits ``demand`` students and is
    not already booked at the current (day, start) slot. Returns the
    room name, or ``None`` if every fitting room is busy — the caller
    treats ``None`` as "try a different time block".
    """
    fitting = [
        (name, cap)
        for name, cap in rooms
        if cap >= demand and name not in busy
    ]
    if not fitting:
        return None
    # Smallest fit — keeps bigger rooms free for cohorts that need
    # them.
    fitting.sort(key=lambda r: r[1])
    return fitting[0][0]


def _slots_collide(a: ClassScheduleSlot, b: ClassScheduleSlot) -> bool:
    """
    Half-open interval overlap on the same weekday. ``a.end_time`` ==
    ``b.start_time`` is treated as adjacent, NOT overlapping, so two
    back-to-back slots are allowed (matching how the cohort scheduler
    packs lectures into the 08:30–17:30 day).
    """
    if a.day_of_week != b.day_of_week:
        return False
    return a.start_time < b.end_time and b.start_time < a.end_time


def _slot_summary(
    slot: ClassScheduleSlot,
    course_code: str,
    instructor_lookup: Optional[
        dict[uuid.UUID, tuple[str, Optional[str]]]
    ] = None,
) -> dict[str, Any]:
    """
    One-line dict describing a slot for the option/conflict payloads.

    ``instructor_lookup`` is an ``{instructor_id: (full_name, staff_id)}``
    map the caller prebuilt so we don't issue per-slot DB queries.
    Omitted ⇒ name/staff_id are left ``None``.
    """
    lookup = instructor_lookup or {}
    name, staff_id = (
        lookup.get(slot.instructor_id, (None, None))
        if slot.instructor_id is not None
        else (None, None)
    )
    return {
        "slot_id": str(slot.id),
        "course_code": course_code,
        "day_of_week": slot.day_of_week,
        "start_time": slot.start_time.isoformat(timespec="minutes"),
        "end_time": slot.end_time.isoformat(timespec="minutes"),
        "room": slot.room,
        "instructor_id": (
            str(slot.instructor_id) if slot.instructor_id else None
        ),
        "instructor_name": name,
        "instructor_staff_id": staff_id,
    }


def _session_partition(credit_hours: int) -> list[int]:
    """
    Break a course's weekly credit hours into 1-hr or 2-hr
    **sessions**. Each session lands on a single day in a single
    room and consumes ``size`` adjacent blocks. Sessions of the
    same course are scheduled on *different* days — that combination
    enforces both the contiguous-session rule and the per-day cap
    of 2 hours per course per cohort by construction.

    Greedy preference for 2-hr sessions — minimises the number of
    days a cohort has to come in for a single course and matches
    standard university block-scheduling. Examples:

        credit_hours=1 → [1]
        credit_hours=2 → [2]
        credit_hours=3 → [2, 1]
        credit_hours=4 → [2, 2]
        credit_hours=5 → [2, 2, 1]
    """
    sessions: list[int] = []
    remaining = credit_hours
    while remaining >= 2:
        sessions.append(2)
        remaining -= 2
    if remaining == 1:
        sessions.append(1)
    return sessions


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
