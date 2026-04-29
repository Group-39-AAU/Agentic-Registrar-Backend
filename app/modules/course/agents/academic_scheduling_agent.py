"""
Academic Scheduling Agent.

Realises SDS Tables 82–84:

    Class AcademicSchedulingAgent
        - instructorSchedule: Map
        - roomInventory: List<Room>
        - timeSlots: List<String>

        + allocateSections(studentList: List<Student>): void
        + generateTimetable(departmentID: String): Schedule
        + resolveRoomConflict(courseID: String, slot: String): Boolean
        + assignInstructor(courseID: String, instructorID: String): void
        + getAvailableRooms(capacityReq: Integer, time: String): List<Room>

The agent is rule-based in Phase 1: it greedily places students into
sections by free capacity, then post-validates the resulting weekly
schedule for room and instructor double-bookings. Anything it cannot
resolve via :meth:`resolve_room_conflict` is recorded as a
:class:`ScheduleConflict` row for the human-visible report listed in
the Track A implementation checklist.

Room inventory and the standard university teaching window
(08:30–17:30, SDS Table 83) are class-level defaults so tests can
inject smaller / larger inventories without touching the singleton.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.course.agents.course_base_agent import CourseBaseAgent
from app.modules.course.models import (
    Course, CourseOffering, InstructorAssignment, Registration,
    ScheduleConflict, Section,
)
from app.shared.enums import (
    RegistrationStatus, ScheduleConflictStatus, ScheduleConflictType,
)


# ── Result containers ────────────────────────────────────────────


@dataclass
class AllocationResult:
    """Outcome of :meth:`allocate_sections` for one term."""
    allocated: list[dict[str, str]] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)


@dataclass
class ScheduleArtefact:
    """
    Snapshot of the per-department weekly schedule plus any
    :class:`ScheduleConflict` rows the agent had to write.
    """
    department: str
    term_id: uuid.UUID
    sections: list[dict[str, Any]] = field(default_factory=list)
    conflict_ids: list[uuid.UUID] = field(default_factory=list)


# ── Agent ────────────────────────────────────────────────────────


class AcademicSchedulingAgent(CourseBaseAgent):
    """SDS §3.1.3 + §5.3 Tables 82–84."""

    AGENT_ID_PREFIX = "AGENT_ASA_"

    # SDS Table 83 invariants — kept identical to the seeded sandbox
    # so tests against seeded data have a coherent baseline.
    DEFAULT_ROOM_INVENTORY: list[tuple[str, int]] = [
        ("NB-101", 40), ("NB-102", 40),
        ("NB-203", 35), ("NB-204", 35), ("NB-305", 30),
        ("FBE-12", 50), ("FBE-14", 50),
    ]
    DEFAULT_TIME_SLOTS: list[str] = [
        "MON 08:30-10:00, WED 08:30-10:00",
        "MON 10:30-12:00, WED 10:30-12:00",
        "TUE 13:30-15:00, THU 13:30-15:00",
        "TUE 15:30-17:00, THU 15:30-17:00",
        "FRI 08:30-11:30",
    ]

    def __init__(
        self,
        agent_id: Optional[str] = None,
        *,
        room_inventory: Optional[list[tuple[str, int]]] = None,
        time_slots: Optional[list[str]] = None,
    ) -> None:
        super().__init__(agent_id=agent_id or f"{self.AGENT_ID_PREFIX}DEFAULT")
        self._rooms: list[tuple[str, int]] = list(
            room_inventory or self.DEFAULT_ROOM_INVENTORY
        )
        self._slots: list[str] = list(time_slots or self.DEFAULT_TIME_SLOTS)

    # ── allocate_sections (SDS Table 84) ─────────────────────────

    async def allocate_sections(
        self, session: AsyncSession, term_id: uuid.UUID,
    ) -> AllocationResult:
        """
        Place every REGISTERED student's chosen courses into a
        section with free capacity. Idempotent — courses already
        allocated (``section_id`` set) are left alone, so the
        officer can re-run scheduling after late drops without
        churning previously-placed students.
        """
        result = AllocationResult()

        registrations = (
            await session.execute(
                select(Registration).where(
                    Registration.term_id == term_id,
                    Registration.status == RegistrationStatus.REGISTERED,
                    Registration.is_deleted == False,  # noqa: E712
                )
            )
        ).scalars().all()

        for reg in registrations:
            await session.refresh(reg, attribute_names=["courses"])
            for rc in reg.courses:
                if rc.is_dropped or rc.section_id is not None:
                    continue

                offering = (
                    await session.execute(
                        select(CourseOffering).where(
                            CourseOffering.course_id == rc.course_id,
                            CourseOffering.term_id == term_id,
                            CourseOffering.is_deleted == False,  # noqa: E712
                        )
                    )
                ).scalar_one_or_none()
                if offering is None:
                    result.failed.append({
                        "student_id": str(reg.student_id),
                        "course_id": str(rc.course_id),
                        "reason": "no offering for course in this term",
                    })
                    continue

                sections = (
                    await session.execute(
                        select(Section).where(
                            Section.offering_id == offering.id,
                            Section.is_deleted == False,  # noqa: E712
                        )
                    )
                ).scalars().all()

                placed = False
                for sec in sections:
                    if sec.enrolled_count < sec.capacity:
                        rc.section_id = sec.id
                        sec.enrolled_count += 1
                        result.allocated.append({
                            "student_id": str(reg.student_id),
                            "course_id": str(rc.course_id),
                            "section_id": str(sec.id),
                        })
                        placed = True
                        break

                if not placed:
                    result.failed.append({
                        "student_id": str(reg.student_id),
                        "course_id": str(rc.course_id),
                        "reason": "all sections full",
                    })

        await session.flush()
        return result

    # ── generate_timetable (SDS Table 84) ────────────────────────

    async def generate_timetable(
        self,
        session: AsyncSession,
        term_id: uuid.UUID,
        department: str,
    ) -> ScheduleArtefact:
        """
        Produces a conflict-free weekly schedule for the given
        department. Tries to auto-resolve room clashes via
        :meth:`resolve_room_conflict`; everything else (notably
        instructor double-bookings) is surfaced as a
        :class:`ScheduleConflict` for the officer's report.
        """
        artefact = ScheduleArtefact(department=department, term_id=term_id)

        rows = (
            await session.execute(
                select(Section, Course).join(
                    CourseOffering, Section.offering_id == CourseOffering.id,
                ).join(
                    Course, CourseOffering.course_id == Course.id,
                ).where(
                    CourseOffering.term_id == term_id,
                    Course.department == department,
                    Section.is_deleted == False,  # noqa: E712
                )
            )
        ).all()
        sections = [(s, c) for s, c in rows]

        # Build clash maps.
        room_at_slot: dict[tuple[str, str], list[Section]] = {}
        instructor_at_slot: dict[tuple[str, uuid.UUID], list[Section]] = {}
        for sec, _course in sections:
            if sec.time_slot and sec.room:
                room_at_slot.setdefault((sec.time_slot, sec.room), []).append(sec)
            if sec.time_slot and sec.instructor_id:
                instructor_at_slot.setdefault(
                    (sec.time_slot, sec.instructor_id), [],
                ).append(sec)

        # Room clashes — try auto-resolve first.
        for (slot, room), clashing_sections in room_at_slot.items():
            if len(clashing_sections) <= 1:
                continue
            for clashing in clashing_sections[1:]:
                resolved = await self.resolve_room_conflict(session, clashing.id)
                if not resolved:
                    conflict = await self._record_conflict(
                        session,
                        term_id=term_id,
                        department=department,
                        conflict_type=ScheduleConflictType.ROOM_DOUBLE_BOOKED,
                        section=clashing,
                        other_section=clashing_sections[0],
                        time_slot=slot,
                        room=room,
                        description=(
                            f"Room {room} double-booked at {slot} "
                            f"by sections {clashing.section_code} and "
                            f"{clashing_sections[0].section_code}."
                        ),
                    )
                    artefact.conflict_ids.append(conflict.id)

        # Instructor clashes — no auto-resolve, surface for officer.
        for (slot, instructor_id), clashing_sections in instructor_at_slot.items():
            if len(clashing_sections) <= 1:
                continue
            for clashing in clashing_sections[1:]:
                conflict = await self._record_conflict(
                    session,
                    term_id=term_id,
                    department=department,
                    conflict_type=ScheduleConflictType.INSTRUCTOR_DOUBLE_BOOKED,
                    section=clashing,
                    other_section=clashing_sections[0],
                    time_slot=slot,
                    instructor_id=instructor_id,
                    description=(
                        f"Instructor double-booked at {slot} across two "
                        "sections in this department."
                    ),
                )
                artefact.conflict_ids.append(conflict.id)

        for sec, course in sections:
            artefact.sections.append({
                "section_id": str(sec.id),
                "course_code": course.code,
                "section_code": sec.section_code,
                "room": sec.room,
                "time_slot": sec.time_slot,
                "instructor_id": str(sec.instructor_id) if sec.instructor_id else None,
                "capacity": sec.capacity,
                "enrolled_count": sec.enrolled_count,
            })

        return artefact

    async def _record_conflict(
        self,
        session: AsyncSession,
        *,
        term_id: uuid.UUID,
        department: str,
        conflict_type: ScheduleConflictType,
        section: Section,
        other_section: Optional[Section] = None,
        time_slot: Optional[str] = None,
        room: Optional[str] = None,
        instructor_id: Optional[uuid.UUID] = None,
        description: str,
    ) -> ScheduleConflict:
        conflict = ScheduleConflict(
            term_id=term_id,
            department=department,
            conflict_type=conflict_type,
            section_id=section.id,
            other_section_id=other_section.id if other_section else None,
            instructor_id=instructor_id,
            time_slot=time_slot,
            room=room,
            description=description,
            detected_by_agent_id=self.agent_id,
            status=ScheduleConflictStatus.OPEN,
        )
        session.add(conflict)
        await session.flush()
        return conflict

    # ── resolve_room_conflict (SDS Table 84) ─────────────────────

    async def resolve_room_conflict(
        self, session: AsyncSession, section_id: uuid.UUID,
    ) -> bool:
        """
        Try to swap ``section`` to an alternative room with capacity
        ≥ ``section.capacity`` not used by any other section at the
        same time slot. Returns True on a successful swap.
        """
        section = await session.get(Section, section_id)
        if section is None or section.time_slot is None:
            return False

        occupied_rows = (
            await session.execute(
                select(Section.room).where(
                    Section.time_slot == section.time_slot,
                    Section.id != section.id,
                    Section.is_deleted == False,  # noqa: E712
                )
            )
        ).scalars().all()
        occupied = {r for r in occupied_rows if r}

        for room_name, room_capacity in self._rooms:
            if room_name == section.room:
                continue
            if room_capacity < section.capacity:
                continue
            if room_name in occupied:
                continue
            section.room = room_name
            await session.flush()
            return True
        return False

    # ── assign_instructor (SDS Table 84) ─────────────────────────

    async def assign_instructor(
        self,
        session: AsyncSession,
        section_id: uuid.UUID,
        instructor_id: uuid.UUID,
        term_id: uuid.UUID,
    ) -> None:
        """
        Pin ``instructor_id`` onto the section and ensure the
        InstructorAssignment row exists for the (instructor, course,
        term) tuple. Idempotent.
        """
        section = await session.get(Section, section_id)
        if section is None:
            raise ValueError(f"Section {section_id} not found.")
        offering = await session.get(CourseOffering, section.offering_id)
        if offering is None:
            raise ValueError(
                f"Offering {section.offering_id} for section {section_id} not found."
            )

        section.instructor_id = instructor_id

        existing = (
            await session.execute(
                select(InstructorAssignment).where(
                    InstructorAssignment.instructor_id == instructor_id,
                    InstructorAssignment.course_id == offering.course_id,
                    InstructorAssignment.term_id == term_id,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(InstructorAssignment(
                instructor_id=instructor_id,
                course_id=offering.course_id,
                term_id=term_id,
            ))
        await session.flush()

    # ── get_available_rooms (SDS Table 84) ───────────────────────

    def get_available_rooms(
        self,
        capacity_req: int,
        occupied_rooms: set[str],
    ) -> list[tuple[str, int]]:
        """
        Pure function: rooms in this agent's inventory whose capacity
        meets ``capacity_req`` and which are not in ``occupied_rooms``.
        Mirrors the SDS Table 84 signature; callers pass an
        already-resolved occupancy set so the function is trivially
        unit-testable.
        """
        return [
            (name, cap) for name, cap in self._rooms
            if cap >= capacity_req and name not in occupied_rooms
        ]

    # ── BaseAgent: process_task aggregates the pipeline ──────────

    async def process_task(
        self, input_data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Run the full scheduling pipeline for a (term, department):

            1. allocate_sections (term-wide)
            2. generate_timetable (per department)

        Returns a structured payload the service layer drops into the
        audit-log metadata column.
        """
        session: AsyncSession = input_data["session"]
        term_id: uuid.UUID = input_data["term_id"]
        department: str = input_data["department"]

        allocation = await self.allocate_sections(session, term_id)
        artefact = await self.generate_timetable(session, term_id, department)

        return {
            "allocation": {
                "allocated_count": len(allocation.allocated),
                "failed_count": len(allocation.failed),
                "allocated": allocation.allocated,
                "failed": allocation.failed,
            },
            "schedule": {
                "department": artefact.department,
                "term_id": str(artefact.term_id),
                "section_count": len(artefact.sections),
                "sections": artefact.sections,
                "conflict_count": len(artefact.conflict_ids),
                "conflict_ids": [str(cid) for cid in artefact.conflict_ids],
            },
        }
