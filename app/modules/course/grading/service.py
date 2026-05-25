"""
Track B (Grading) — service layer.

PR 1 surface:

  * ``InstructorGradingService.list_my_section_assignments`` — every
    (section, course) pair the calling instructor teaches in a term,
    derived from ``ClassScheduleSlot`` rows.
  * ``InstructorGradingService.get_section_course_roster`` — the
    effective roster for one (section, course), enforcing the
    instructor's ownership of the slot before any data is returned.

PR 2 surface:

  * ``upsert_breakdown``       — create / replace the per-(section,
    course) assessment breakdown, enforcing the sum-to-100 invariant
    and the lock-on-first-entry rule.
  * ``get_breakdown``          — read.
  * ``get_or_create_batch``    — idempotently materialise the
    ``GradeBatch`` for a (section, course) once a breakdown exists.
  * ``upsert_scores``          — bulk save raw component scores;
    locks the breakdown on first non-null save.
  * ``submit_batch``           — validate completeness, compute
    weighted numeric + letter for every student, upsert ``Grade``
    rows, ask the (stub) agent for a verdict, transition the batch
    out of DRAFT.

The service uses the same auth-failure / not-found exception classes
already defined for the course module so the router maps them to
HTTP status codes the same way it does for Track A endpoints.
"""
from __future__ import annotations

import math
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm_client import LLMClient
from app.modules.course.exceptions import (
    BreakdownLockedError, EntityNotFoundError, GradeBatchNotEditableError,
    IncompleteGradeSubmissionError, InvalidBreakdownError,
    UnauthorizedActorError,
)
from app.modules.course.grade_points import points_for
from app.modules.auth.models import User as _AuthUser
from app.modules.course.models import (
    AcademicTerm, ClassScheduleSlot, Course, Grade, Instructor, Section,
)
from app.modules.course.grading.agents import (
    GradingMonitorAgent, GradingReview,
)
from app.modules.course.grading.letter_scale import letter_for_numeric
from app.modules.course.grading.models import (
    AssessmentBreakdown, AssessmentComponent, GradeAgentReview, GradeBatch,
    StudentComponentScore,
)
from app.modules.course.grading.roster import (
    RosterMember, derive_section_course_roster, section_exists,
)
from app.modules.course.grading.schemas import (
    AssessmentBreakdownCreate, AssessmentBreakdownResponse,
    AssessmentComponentResponse, BulkScoreWrite, GradeAgentReviewResponse,
    GradeBatchResponse, GradeBatchSubmitResponse,
    InstructorSectionAssignmentResponse,
    RosterStudentResponse, SectionCourseRosterResponse,
    StudentBatchRowResponse, StudentScoreCellResponse, SubmittedGradeRow,
)
from app.shared.enums import GradeSubmissionStatus


# Tolerance for the sum-of-weights == 100 invariant. Components are
# floats so a literal == comparison would reject 33.33 + 33.33 + 33.34.
_WEIGHT_SUM_TOLERANCE = 1e-6


class InstructorGradingService:
    """
    Read-side service for the grading workflow's instructor surface.
    Resolves the calling user's Instructor profile, materialises the
    list of (section, course) pairs they teach in a term, and serves
    the effective roster of each pair.
    """

    def __init__(
        self,
        db: AsyncSession,
        *,
        grading_agent: Optional[GradingMonitorAgent] = None,
    ) -> None:
        self.db = db
        # Lazy default: build the production agent (with the default
        # LLM client) on first use. Tests inject a stub agent so they
        # don't depend on the GEMINI_API_KEY being set.
        self._agent = grading_agent

    def _resolve_agent(self) -> GradingMonitorAgent:
        """
        Lazily build the production :class:`GradingMonitorAgent`
        with the default LLM client. Cached on the instance so a
        single request only constructs it once.
        """
        if self._agent is None:
            from app.ai.llm_client import build_default_llm_client
            self._agent = GradingMonitorAgent(
                llm_client=build_default_llm_client(),
            )
        return self._agent

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

    # ════════════════════════════════════════════════════════════
    #  PR 2 — Breakdown editor + grade-entry batch
    # ════════════════════════════════════════════════════════════

    # ── Ownership helper (shared by every PR 2 endpoint) ──

    async def _resolve_owned_pair_or_403(
        self,
        *,
        user_id: uuid.UUID,
        section_id: uuid.UUID,
        course_id: uuid.UUID,
    ) -> tuple[Instructor, Section, Course]:
        """
        Resolve the calling Instructor and the (Section, Course) rows
        for ``(section_id, course_id)``, enforcing that the caller
        actually teaches the pair. Raises the same exceptions the
        roster endpoint does so the router maps them identically.
        """
        instructor = await self._resolve_instructor_or_403(user_id)
        if not await section_exists(self.db, section_id):
            raise EntityNotFoundError("Section", str(section_id))

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
        return instructor, section, course

    # ── Breakdown CRUD ──

    @staticmethod
    def _validate_breakdown_payload(payload: AssessmentBreakdownCreate) -> None:
        """
        Apply the policy invariants the route docs promise:

          - At least one component (Pydantic already enforces).
          - No duplicate component names within the breakdown.
          - sum(weights) == 100 within floating-point tolerance.
        """
        names = [c.name.strip() for c in payload.components]
        if len(set(names)) != len(names):
            raise InvalidBreakdownError(
                "Component names must be unique within a breakdown."
            )
        total = sum(c.weight for c in payload.components)
        if not math.isclose(total, 100.0, abs_tol=_WEIGHT_SUM_TOLERANCE):
            raise InvalidBreakdownError(
                f"Component weights must sum to exactly 100 "
                f"(got {total:g})."
            )

    async def upsert_breakdown(
        self,
        *,
        user_id: uuid.UUID,
        section_id: uuid.UUID,
        course_id: uuid.UUID,
        payload: AssessmentBreakdownCreate,
    ) -> AssessmentBreakdownResponse:
        """
        Create or replace the breakdown for ``(section_id, course_id)``.

        - If no breakdown exists, create one at ``version=1``.
        - If a breakdown exists and is NOT locked, replace its
          components atomically and bump ``version``.
        - If a breakdown exists and IS locked (any score saved),
          refuse the write with ``BreakdownLockedError``.
        """
        self._validate_breakdown_payload(payload)

        instructor, section, _course = await self._resolve_owned_pair_or_403(
            user_id=user_id, section_id=section_id, course_id=course_id,
        )

        existing = (
            await self.db.execute(
                select(AssessmentBreakdown).where(
                    AssessmentBreakdown.section_id == section_id,
                    AssessmentBreakdown.course_id == course_id,
                    AssessmentBreakdown.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()

        if existing is None:
            breakdown = AssessmentBreakdown(
                section_id=section_id,
                course_id=course_id,
                instructor_id=instructor.id,
                term_id=section.term_id,
                version=1,
                created_by_id=user_id,
            )
            self.db.add(breakdown)
            await self.db.flush()
        else:
            if existing.locked_at is not None:
                raise BreakdownLockedError()
            # Wipe the old components and replace. ORM cascade on the
            # relationship is delete-orphan, so emptying the list
            # deletes the children.
            existing.components.clear()
            await self.db.flush()
            existing.version += 1
            breakdown = existing

        # Add fresh components in payload order; ``order_index``
        # preserves the instructor's intended display sequence.
        for idx, comp in enumerate(payload.components):
            self.db.add(
                AssessmentComponent(
                    breakdown_id=breakdown.id,
                    name=comp.name.strip(),
                    weight=comp.weight,
                    max_score=comp.max_score,
                    order_index=idx,
                )
            )
        await self.db.flush()
        await self.db.commit()

        # Re-fetch to populate the components relationship.
        await self.db.refresh(breakdown, attribute_names=["components"])
        return self._breakdown_to_schema(breakdown)

    async def get_breakdown(
        self,
        *,
        user_id: uuid.UUID,
        section_id: uuid.UUID,
        course_id: uuid.UUID,
    ) -> AssessmentBreakdownResponse:
        """
        Read the breakdown for ``(section_id, course_id)``. 404 if
        the breakdown doesn't exist yet (the instructor has to POST
        one first).
        """
        await self._resolve_owned_pair_or_403(
            user_id=user_id, section_id=section_id, course_id=course_id,
        )

        breakdown = (
            await self.db.execute(
                select(AssessmentBreakdown).where(
                    AssessmentBreakdown.section_id == section_id,
                    AssessmentBreakdown.course_id == course_id,
                    AssessmentBreakdown.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()
        if breakdown is None:
            raise EntityNotFoundError(
                "AssessmentBreakdown",
                f"section={section_id} course={course_id}",
            )
        return self._breakdown_to_schema(breakdown)

    @staticmethod
    def _breakdown_to_schema(
        breakdown: AssessmentBreakdown,
    ) -> AssessmentBreakdownResponse:
        return AssessmentBreakdownResponse(
            id=breakdown.id,
            section_id=breakdown.section_id,
            course_id=breakdown.course_id,
            instructor_id=breakdown.instructor_id,
            term_id=breakdown.term_id,
            version=breakdown.version,
            locked_at=breakdown.locked_at,
            components=[
                AssessmentComponentResponse(
                    id=c.id, name=c.name, weight=c.weight,
                    max_score=c.max_score, order_index=c.order_index,
                )
                for c in sorted(
                    breakdown.components, key=lambda c: c.order_index,
                )
            ],
        )

    # ── Batch get-or-create + reads ──

    async def get_or_create_batch(
        self,
        *,
        user_id: uuid.UUID,
        section_id: uuid.UUID,
        course_id: uuid.UUID,
    ) -> GradeBatchResponse:
        """
        Idempotent: returns the existing ``GradeBatch`` for the pair,
        or creates an empty DRAFT one if none exists yet. Requires
        that a breakdown already exists.
        """
        instructor, section, course = await self._resolve_owned_pair_or_403(
            user_id=user_id, section_id=section_id, course_id=course_id,
        )

        breakdown = (
            await self.db.execute(
                select(AssessmentBreakdown).where(
                    AssessmentBreakdown.section_id == section_id,
                    AssessmentBreakdown.course_id == course_id,
                    AssessmentBreakdown.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()
        if breakdown is None:
            raise EntityNotFoundError(
                "AssessmentBreakdown",
                f"section={section_id} course={course_id}",
            )

        batch = (
            await self.db.execute(
                select(GradeBatch).where(
                    GradeBatch.section_id == section_id,
                    GradeBatch.course_id == course_id,
                    GradeBatch.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()
        if batch is None:
            batch = GradeBatch(
                section_id=section_id,
                course_id=course_id,
                instructor_id=instructor.id,
                term_id=section.term_id,
                breakdown_id=breakdown.id,
                status=GradeSubmissionStatus.DRAFT,
            )
            self.db.add(batch)
            await self.db.flush()
            await self.db.commit()

        return await self._build_batch_response(
            batch=batch, breakdown=breakdown, section=section, course=course,
        )

    async def _build_batch_response(
        self,
        *,
        batch: GradeBatch,
        breakdown: AssessmentBreakdown,
        section: Section,
        course: Course,
    ) -> GradeBatchResponse:
        """Materialise the live score matrix view for a batch."""
        roster = await derive_section_course_roster(
            self.db, section_id=batch.section_id, course_id=batch.course_id,
        )
        roster_by_student = {m.student_id: m for m in roster}

        # Pull every score row for this batch in one query.
        existing_scores = (
            await self.db.execute(
                select(StudentComponentScore).where(
                    StudentComponentScore.batch_id == batch.id,
                )
            )
        ).scalars().all()
        score_by_cell: dict[tuple[uuid.UUID, uuid.UUID], float | None] = {}
        for s in existing_scores:
            score_by_cell[(s.student_id, s.component_id)] = s.score

        components = sorted(breakdown.components, key=lambda c: c.order_index)
        rows: list[StudentBatchRowResponse] = []
        for member in roster:
            cells: list[StudentScoreCellResponse] = []
            complete = True
            for comp in components:
                val = score_by_cell.get((member.student_id, comp.id))
                if val is None:
                    complete = False
                cells.append(
                    StudentScoreCellResponse(
                        student_id=member.student_id,
                        component_id=comp.id,
                        score=val,
                    )
                )
            rows.append(
                StudentBatchRowResponse(
                    student_id=member.student_id,
                    student_number=member.student_number,
                    full_name=member.full_name,
                    is_added_via_drop=member.is_added_via_drop,
                    is_complete=complete and len(components) > 0,
                    scores=cells,
                )
            )

        term = await self.db.get(AcademicTerm, batch.term_id)
        instructor_row = await self.db.get(Instructor, batch.instructor_id)
        instructor_user = (
            await self.db.get(_AuthUser, instructor_row.user_id)
            if instructor_row is not None
            else None
        )
        instructor_name = (
            f"{instructor_user.first_name} {instructor_user.last_name}".strip()
            if instructor_user is not None
            else ""
        )

        return GradeBatchResponse(
            id=batch.id,
            section_id=batch.section_id,
            section_code=section.section_code,
            section_semester=section.semester,
            course_id=batch.course_id,
            course_code=course.code,
            course_title=course.title,
            course_credit_hours=course.credit_hours,
            term_id=batch.term_id,
            term_name=term.term_name if term is not None else "",
            instructor_id=batch.instructor_id,
            instructor_name=instructor_name,
            breakdown_id=batch.breakdown_id,
            status=batch.status,
            iteration_count=batch.iteration_count,
            submitted_at=batch.submitted_at,
            instructor_justification=batch.instructor_justification,
            breakdown=self._breakdown_to_schema(breakdown),
            rows=rows,
        )

    # ── Score upsert (draft save) ──

    async def upsert_scores(
        self,
        *,
        user_id: uuid.UUID,
        batch_id: uuid.UUID,
        payload: BulkScoreWrite,
    ) -> GradeBatchResponse:
        """
        Upsert a set of score cells in one transaction. Validates:

          - Batch exists and is in DRAFT.
          - Caller owns the (section, course).
          - Every cell's student is on the live roster (rejects
            stale frontend data referring to a no-longer-enrolled
            student).
          - Every cell's component belongs to the batch's breakdown.
          - Every non-null score satisfies ``0 <= score <= max_score``.

        On first non-null save, the breakdown's ``locked_at`` is set
        so any future breakdown edit is refused.
        """
        batch = (
            await self.db.execute(
                select(GradeBatch).where(
                    GradeBatch.id == batch_id,
                    GradeBatch.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()
        if batch is None:
            raise EntityNotFoundError("GradeBatch", str(batch_id))
        if batch.status != GradeSubmissionStatus.DRAFT:
            raise GradeBatchNotEditableError(batch.status.value)

        _instructor, section, course = await self._resolve_owned_pair_or_403(
            user_id=user_id,
            section_id=batch.section_id, course_id=batch.course_id,
        )

        # Pull the components for max_score lookup.
        components = (
            await self.db.execute(
                select(AssessmentComponent).where(
                    AssessmentComponent.breakdown_id == batch.breakdown_id,
                )
            )
        ).scalars().all()
        comp_by_id = {c.id: c for c in components}

        # Pull the live roster so we can reject stale student IDs.
        roster = await derive_section_course_roster(
            self.db, section_id=batch.section_id, course_id=batch.course_id,
        )
        roster_ids = {m.student_id for m in roster}

        # Validate every cell *before* writing any of them.
        for cell in payload.cells:
            if cell.student_id not in roster_ids:
                raise InvalidBreakdownError(
                    f"Student {cell.student_id} is not on the live roster "
                    f"for this (section, course)."
                )
            comp = comp_by_id.get(cell.component_id)
            if comp is None:
                raise InvalidBreakdownError(
                    f"Component {cell.component_id} does not belong to "
                    f"this batch's breakdown."
                )
            if cell.score is not None and cell.score > comp.max_score:
                raise InvalidBreakdownError(
                    f"Score {cell.score} for component '{comp.name}' "
                    f"exceeds its max_score of {comp.max_score}."
                )

        # Upsert: fetch existing rows for these (student, component)
        # pairs in one query, then update-in-place or insert.
        cell_keys = [
            (c.student_id, c.component_id) for c in payload.cells
        ]
        existing_rows = (
            await self.db.execute(
                select(StudentComponentScore).where(
                    StudentComponentScore.batch_id == batch_id,
                )
            )
        ).scalars().all()
        existing_by_key = {
            (r.student_id, r.component_id): r for r in existing_rows
        }

        any_non_null = False
        for cell in payload.cells:
            if cell.score is not None:
                any_non_null = True
            row = existing_by_key.get((cell.student_id, cell.component_id))
            if row is None:
                self.db.add(
                    StudentComponentScore(
                        batch_id=batch_id,
                        student_id=cell.student_id,
                        component_id=cell.component_id,
                        score=cell.score,
                    )
                )
            else:
                row.score = cell.score

        # Lock the breakdown on first non-null save.
        if any_non_null:
            breakdown = (
                await self.db.execute(
                    select(AssessmentBreakdown).where(
                        AssessmentBreakdown.id == batch.breakdown_id,
                    )
                )
            ).scalar_one()
            if breakdown.locked_at is None:
                breakdown.locked_at = datetime.now(timezone.utc)

        await self.db.flush()
        await self.db.commit()

        breakdown = (
            await self.db.execute(
                select(AssessmentBreakdown).where(
                    AssessmentBreakdown.id == batch.breakdown_id,
                )
            )
        ).scalar_one()
        return await self._build_batch_response(
            batch=batch, breakdown=breakdown, section=section, course=course,
        )

    # ── Clear scores + unlock breakdown ──

    async def delete_all_scores(
        self,
        *,
        user_id: uuid.UUID,
        batch_id: uuid.UUID,
    ) -> GradeBatchResponse:
        """
        Wipe every ``StudentComponentScore`` for the batch and reset
        the breakdown's ``locked_at`` to NULL so the instructor can
        re-POST a new breakdown.

        Allowed only while the batch is in DRAFT — once SUBMITTED or
        beyond, edits go through the iteration / DH-review path
        (PR 4), not via score deletion.

        The batch row itself is preserved (same id, status stays
        DRAFT) so any frontend reference keeps working; only the
        cells inside it are cleared.
        """
        batch = (
            await self.db.execute(
                select(GradeBatch).where(
                    GradeBatch.id == batch_id,
                    GradeBatch.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()
        if batch is None:
            raise EntityNotFoundError("GradeBatch", str(batch_id))
        if batch.status != GradeSubmissionStatus.DRAFT:
            raise GradeBatchNotEditableError(batch.status.value)

        _instructor, section, course = await self._resolve_owned_pair_or_403(
            user_id=user_id,
            section_id=batch.section_id, course_id=batch.course_id,
        )

        # Wipe the score cells in one DELETE.
        existing_scores = (
            await self.db.execute(
                select(StudentComponentScore).where(
                    StudentComponentScore.batch_id == batch_id,
                )
            )
        ).scalars().all()
        for s in existing_scores:
            await self.db.delete(s)

        # Unlock the breakdown so a fresh POST is accepted again.
        breakdown = (
            await self.db.execute(
                select(AssessmentBreakdown).where(
                    AssessmentBreakdown.id == batch.breakdown_id,
                )
            )
        ).scalar_one()
        breakdown.locked_at = None

        await self.db.flush()
        await self.db.commit()

        return await self._build_batch_response(
            batch=batch, breakdown=breakdown, section=section, course=course,
        )

    # ── Submit ──

    async def submit_batch(
        self,
        *,
        user_id: uuid.UUID,
        batch_id: uuid.UUID,
    ) -> GradeBatchSubmitResponse:
        """
        Transition the batch DRAFT → SUBMITTED:

          1. Refuse if any roster student is missing any component score.
          2. Compute weighted_pct per student
             = sum((score_i / max_score_i) * weight_i).
          3. Map the percentage to a letter via the AAU scale.
          4. Upsert ``Grade`` rows for every roster student.
          5. Ask the (stub) agent for a verdict.
          6. Persist new batch status and submission audit fields.

        Returns the verdict and the per-student outcomes for display.
        """
        batch = (
            await self.db.execute(
                select(GradeBatch).where(
                    GradeBatch.id == batch_id,
                    GradeBatch.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()
        if batch is None:
            raise EntityNotFoundError("GradeBatch", str(batch_id))
        if batch.status != GradeSubmissionStatus.DRAFT:
            raise GradeBatchNotEditableError(batch.status.value)

        _instructor, section, course = await self._resolve_owned_pair_or_403(
            user_id=user_id,
            section_id=batch.section_id, course_id=batch.course_id,
        )

        # Re-validate breakdown invariant at submit time as a final
        # guard. The instructor cannot have edited it post-lock, but
        # this catches any data-quality drift from direct DB edits.
        components = sorted(
            (await self.db.execute(
                select(AssessmentComponent).where(
                    AssessmentComponent.breakdown_id == batch.breakdown_id,
                )
            )).scalars().all(),
            key=lambda c: c.order_index,
        )
        total_weight = sum(c.weight for c in components)
        if not math.isclose(
            total_weight, 100.0, abs_tol=_WEIGHT_SUM_TOLERANCE,
        ):
            raise InvalidBreakdownError(
                f"Breakdown weights do not sum to 100 (got {total_weight:g})."
            )

        roster = await derive_section_course_roster(
            self.db, section_id=batch.section_id, course_id=batch.course_id,
        )
        if not roster:
            raise InvalidBreakdownError(
                "Cannot submit an empty batch — the roster has no students."
            )

        scores = (
            await self.db.execute(
                select(StudentComponentScore).where(
                    StudentComponentScore.batch_id == batch_id,
                )
            )
        ).scalars().all()
        score_by_cell: dict[tuple[uuid.UUID, uuid.UUID], float | None] = {
            (s.student_id, s.component_id): s.score for s in scores
        }

        # Detect missing cells before computing anything.
        missing: list[dict] = []
        for member in roster:
            missing_components = [
                c.name for c in components
                if score_by_cell.get((member.student_id, c.id)) is None
            ]
            if missing_components:
                missing.append({
                    "student_id": str(member.student_id),
                    "student_number": member.student_number,
                    "full_name": member.full_name,
                    "missing_components": missing_components,
                })
        if missing:
            raise IncompleteGradeSubmissionError(missing)

        # Compute per-student outcomes and upsert Grade rows.
        outcomes = self._compute_outcomes(
            roster=roster, components=components,
            score_by_cell=score_by_cell,
        )
        now = datetime.now(timezone.utc)
        await self._upsert_grade_rows(
            batch=batch, course=course, outcomes=outcomes,
            user_id=user_id, now=now,
        )

        # Capture the submission timestamp on the batch BEFORE the
        # agent runs so the deadline-status tool sees it.
        batch.submitted_at = now
        batch.submitted_by_id = user_id
        await self.db.flush()

        # Always-LLM agent — verdict comes from the LLM. On failure
        # the agent returns PENDING and the batch stays SUBMITTED.
        agent = self._resolve_agent()
        review = await agent.review_batch(db=self.db, batch_id=batch.id)
        await self._persist_agent_review(
            batch=batch, review=review, agent=agent,
        )

        # Transition batch state per the LLM verdict.
        if review.verdict == "APPROVE":
            batch.status = GradeSubmissionStatus.SUBMITTED
        elif review.verdict == "FLAG":
            batch.status = GradeSubmissionStatus.FLAGGED
        else:  # PENDING
            # Stay at SUBMITTED — the DH re-trigger path (PR 4) will
            # produce a real verdict later.
            batch.status = GradeSubmissionStatus.SUBMITTED
        await self.db.flush()
        await self.db.commit()

        return GradeBatchSubmitResponse(
            batch_id=batch.id,
            status=batch.status,
            iteration=batch.iteration_count,
            submitted_at=now,
            agent_verdict=review.verdict,
            agent_flags=review.flags,
            agent_reasoning=review.reasoning,
            grades=[
                SubmittedGradeRow(
                    student_id=o["student_id"],
                    student_number=o["student_number"],
                    full_name=o["full_name"],
                    numeric_score=o["numeric"],
                    letter_grade=o["letter"],
                )
                for o in outcomes
            ],
        )

    async def _persist_agent_review(
        self,
        *,
        batch: GradeBatch,
        review: GradingReview,
        agent: GradingMonitorAgent,
    ) -> GradeAgentReview:
        """
        Append one row to ``grade_agent_reviews`` and audit-log it.
        Append-only — never updates an existing row.
        """
        row = GradeAgentReview(
            batch_id=batch.id,
            iteration=batch.iteration_count,
            verdict=review.verdict,
            tool_findings=review.tool_findings,
            llm_reasoning=review.reasoning or None,
            flags=review.flags,
            agent_id=review.agent_id,
        )
        self.db.add(row)
        await self.db.flush()
        await agent._log_action(
            self.db,
            action="grading_agent_review",
            resource_type="GradeBatch",
            resource_id=batch.id,
            decision=review.verdict,
            metadata={
                "iteration": batch.iteration_count,
                "flag_count": len(review.flags),
            },
        )
        return row

    @staticmethod
    def _compute_outcomes(
        *,
        roster: list[RosterMember],
        components: list[AssessmentComponent],
        score_by_cell: dict[tuple[uuid.UUID, uuid.UUID], float | None],
    ) -> list[dict]:
        """
        Compute the weighted-percent total and AAU letter for every
        student. Pure function — no DB access — so it's covered by
        unit tests without fixtures.
        """
        outcomes: list[dict] = []
        for member in roster:
            weighted = 0.0
            for comp in components:
                raw = score_by_cell[(member.student_id, comp.id)]
                # raw is non-null here — submit_batch checked missing.
                weighted += (raw / comp.max_score) * comp.weight
            # Clamp tiny float overflows like 100.00000000001.
            weighted = max(0.0, min(100.0, weighted))
            letter = letter_for_numeric(weighted)
            outcomes.append({
                "student_id": member.student_id,
                "student_number": member.student_number,
                "full_name": member.full_name,
                "numeric": round(weighted, 4),
                "letter": letter,
            })
        return outcomes

    async def _upsert_grade_rows(
        self,
        *,
        batch: GradeBatch,
        course: Course,
        outcomes: list[dict],
        user_id: uuid.UUID,
        now: datetime,
    ) -> None:
        """
        Write one ``Grade`` row per student. Status lands at SUBMITTED
        — the DH authorisation in PR 4 will flip them to AUTHORISED
        (the value Track A's CGPA calculator filters on).
        """
        student_ids = [o["student_id"] for o in outcomes]
        existing_grades = (
            await self.db.execute(
                select(Grade).where(
                    Grade.student_id.in_(student_ids),
                    Grade.course_id == course.id,
                    Grade.term_id == batch.term_id,
                )
            )
        ).scalars().all()
        grade_by_student = {g.student_id: g for g in existing_grades}

        for o in outcomes:
            points = points_for(o["letter"])
            grade_points = (
                course.credit_hours * points if points is not None else None
            )
            row = grade_by_student.get(o["student_id"])
            if row is None:
                self.db.add(
                    Grade(
                        student_id=o["student_id"],
                        course_id=course.id,
                        term_id=batch.term_id,
                        section_id=batch.section_id,
                        letter_grade=o["letter"],
                        numeric_score=o["numeric"],
                        credit_hours=course.credit_hours,
                        grade_points=grade_points,
                        status=GradeSubmissionStatus.SUBMITTED,
                        entered_by_id=user_id,
                        entered_at=now,
                    )
                )
            else:
                row.letter_grade = o["letter"]
                row.numeric_score = o["numeric"]
                row.credit_hours = course.credit_hours
                row.grade_points = grade_points
                row.status = GradeSubmissionStatus.SUBMITTED
                row.entered_by_id = user_id
                row.entered_at = now
                row.section_id = batch.section_id
        await self.db.flush()

    # ── PR 3 — iteration loop (justify / reopen) ───────────────

    async def submit_justification(
        self,
        *,
        user_id: uuid.UUID,
        batch_id: uuid.UUID,
        justification: str,
    ) -> GradeBatchSubmitResponse:
        """
        Attach an instructor justification to a FLAGGED batch and
        re-run the agent. The justification is appended to the
        agent's context so the LLM can reconsider its prior FLAG in
        light of the new information.

        Bumps ``iteration_count`` and writes a fresh
        ``grade_agent_reviews`` row. The verdict on iteration 2+ may
        be APPROVE (in which case the batch moves to SUBMITTED) or
        another FLAG (the DH ultimately decides in PR 4).

        Allowed only from status FLAGGED — a FLAGGED batch is what
        an instructor justification is for. From any other state
        this is a 409.
        """
        batch = (
            await self.db.execute(
                select(GradeBatch).where(
                    GradeBatch.id == batch_id,
                    GradeBatch.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()
        if batch is None:
            raise EntityNotFoundError("GradeBatch", str(batch_id))
        if batch.status != GradeSubmissionStatus.FLAGGED:
            raise GradeBatchNotEditableError(batch.status.value)

        _instructor, section, course = await self._resolve_owned_pair_or_403(
            user_id=user_id,
            section_id=batch.section_id, course_id=batch.course_id,
        )

        batch.instructor_justification = justification.strip()
        batch.iteration_count += 1
        await self.db.flush()

        # Re-run the agent. Context now includes the justification
        # (the agent picks it up from batch.instructor_justification).
        agent = self._resolve_agent()
        review = await agent.review_batch(db=self.db, batch_id=batch.id)
        await self._persist_agent_review(
            batch=batch, review=review, agent=agent,
        )

        if review.verdict == "APPROVE":
            batch.status = GradeSubmissionStatus.SUBMITTED
        # FLAG / PENDING → leave status as FLAGGED. The DH workflow
        # (PR 4) is what moves a FLAGGED batch out either way.

        await self.db.flush()
        await self.db.commit()

        # Recompute the outcomes from the existing scores for the
        # response payload — the per-student letters didn't change
        # (no score edits) but the caller wants them anyway.
        roster = await derive_section_course_roster(
            self.db, section_id=batch.section_id, course_id=batch.course_id,
        )
        components = sorted(
            (await self.db.execute(
                select(AssessmentComponent).where(
                    AssessmentComponent.breakdown_id == batch.breakdown_id,
                )
            )).scalars().all(),
            key=lambda c: c.order_index,
        )
        scores = (await self.db.execute(
            select(StudentComponentScore).where(
                StudentComponentScore.batch_id == batch.id,
            )
        )).scalars().all()
        score_by_cell = {
            (s.student_id, s.component_id): s.score for s in scores
        }
        outcomes = self._compute_outcomes(
            roster=roster, components=components,
            score_by_cell=score_by_cell,
        )

        return GradeBatchSubmitResponse(
            batch_id=batch.id,
            status=batch.status,
            iteration=batch.iteration_count,
            submitted_at=batch.submitted_at or datetime.now(timezone.utc),
            agent_verdict=review.verdict,
            agent_flags=review.flags,
            agent_reasoning=review.reasoning,
            grades=[
                SubmittedGradeRow(
                    student_id=o["student_id"],
                    student_number=o["student_number"],
                    full_name=o["full_name"],
                    numeric_score=o["numeric"],
                    letter_grade=o["letter"],
                )
                for o in outcomes
            ],
        )

    async def reopen_batch(
        self,
        *,
        user_id: uuid.UUID,
        batch_id: uuid.UUID,
    ) -> GradeBatchResponse:
        """
        Move a FLAGGED or REJECTED batch back to DRAFT so the
        instructor can edit scores again. The breakdown stays locked
        (history of scores survives in the cells); to reset the
        breakdown the instructor must additionally call
        ``DELETE /scores``.

        Bumps ``iteration_count`` so the next submit's agent review
        lands on the right iteration row.

        Two entry points use this:

          - Instructor accepts the agent's FLAG and wants to fix
            scores rather than justify (FLAGGED → DRAFT).
          - DH REJECTS the batch and the instructor needs to redo
            the work (REJECTED → DRAFT).

        From any other status this is a 409.
        """
        batch = (
            await self.db.execute(
                select(GradeBatch).where(
                    GradeBatch.id == batch_id,
                    GradeBatch.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()
        if batch is None:
            raise EntityNotFoundError("GradeBatch", str(batch_id))
        if batch.status not in {
            GradeSubmissionStatus.FLAGGED,
            GradeSubmissionStatus.REJECTED,
        }:
            raise GradeBatchNotEditableError(batch.status.value)

        _instructor, section, course = await self._resolve_owned_pair_or_403(
            user_id=user_id,
            section_id=batch.section_id, course_id=batch.course_id,
        )

        batch.status = GradeSubmissionStatus.DRAFT
        batch.iteration_count += 1
        # Clear submission-time fields so a fresh submit records anew.
        batch.submitted_at = None
        batch.submitted_by_id = None
        # Keep instructor_justification on the row for the audit trail.

        await self.db.flush()
        await self.db.commit()

        breakdown = (
            await self.db.execute(
                select(AssessmentBreakdown).where(
                    AssessmentBreakdown.id == batch.breakdown_id,
                )
            )
        ).scalar_one()
        return await self._build_batch_response(
            batch=batch, breakdown=breakdown, section=section, course=course,
        )

    # ── PR 3 — agent-review history read ────────────────────────

    async def list_agent_reviews(
        self,
        *,
        user_id: uuid.UUID,
        batch_id: uuid.UUID,
    ) -> list[GradeAgentReviewResponse]:
        """
        Append-only history of every agent run for this batch.
        Most-recent-first so the UI shows the latest verdict at the
        top.
        """
        batch = (
            await self.db.execute(
                select(GradeBatch).where(
                    GradeBatch.id == batch_id,
                    GradeBatch.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()
        if batch is None:
            raise EntityNotFoundError("GradeBatch", str(batch_id))
        # Ownership gate — only the assigned instructor (and later,
        # the DH; PR 4 adds that role check) can read the history.
        await self._resolve_owned_pair_or_403(
            user_id=user_id,
            section_id=batch.section_id, course_id=batch.course_id,
        )

        rows = (await self.db.execute(
            select(GradeAgentReview)
            .where(GradeAgentReview.batch_id == batch_id)
            # iteration is the authoritative ordering; created_at is
            # only a tiebreak (rapid same-iteration reruns from a
            # retry loop). SQLite truncates timestamps to seconds in
            # some drivers, so iteration must come first.
            .order_by(
                GradeAgentReview.iteration.desc(),
                GradeAgentReview.created_at.desc(),
            )
        )).scalars().all()
        return [
            GradeAgentReviewResponse(
                id=r.id,
                iteration=r.iteration,
                verdict=r.verdict,
                flags=r.flags or [],
                llm_reasoning=r.llm_reasoning,
                tool_findings=r.tool_findings or {},
                agent_id=r.agent_id,
                created_at=r.created_at,
            )
            for r in rows
        ]
