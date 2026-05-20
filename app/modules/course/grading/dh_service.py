"""
Track B (Grading) — Department-head service layer (PR 4).

Owns:

  * ``list_queue``       — every batch awaiting a DH decision
                           (status SUBMITTED or FLAGGED), sorted
                           by submitted_at ascending so the oldest
                           surfaces first.
  * ``get_review_packet``— full review payload for one batch:
                           breakdown, score matrix, per-student
                           computed letters, every agent run, every
                           prior DH decision.
  * ``authorise``        — flip the batch to AUTHORISED and flip
                           the per-student ``Grade`` rows to
                           AUTHORISED so Track A's CGPA calculator
                           picks them up. Records the immutable
                           audit row. Justification optional only
                           when the agent's latest verdict was
                           APPROVE — overriding a FLAG requires a
                           written reason.
  * ``reject``           — flip the batch to REJECTED and the
                           ``Grade`` rows to REJECTED so they
                           never count toward CGPA. Justification
                           always required (the instructor reads
                           it). Records the immutable audit row.
  * ``rerun_agent``      — for PENDING-verdict batches: re-invoke
                           the GradingMonitorAgent. No DH decision
                           is written; the batch transitions on the
                           new verdict.

The service lives in its own module rather than extending
``service.py``'s ``InstructorGradingService`` because the role-gate
(DEPARTMENT_HEAD) and the methods are entirely DH-facing — keeping
them separate makes the auth contract obvious at a glance.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import User
from app.modules.course.exceptions import (
    DepartmentHeadRoleRequiredError, EntityNotFoundError,
    GradeBatchNotReviewableError, JustificationRequiredError,
)
from app.modules.course.grading.agents import GradingMonitorAgent
from app.modules.course.grading.letter_scale import letter_for_numeric
from app.modules.course.grading.models import (
    AssessmentBreakdown, AssessmentComponent, GradeAgentReview,
    GradeAuthorisationDecision, GradeBatch, StudentComponentScore,
)
from app.modules.course.grading.roster import (
    derive_section_course_roster,
)
from app.modules.course.grading.schemas import (
    AgentRerunResponse, DepartmentHeadDecisionResponse,
    DepartmentHeadQueueEntry, GradeAgentReviewResponse,
    GradeAuthorisationDecisionResponse, GradeBatchReviewPacketResponse,
    QueueDepartmentOption, SubmittedGradeRow,
)
from app.modules.course.models import (
    AcademicTerm, Course, CourseManagementOfficer, Grade, Instructor,
    Section,
)
from app.shared.enums import (
    GradeSubmissionStatus, OfficerRole, UserRole,
)


# Decision values mirrored from the model's CHECK constraint.
_DECISION_AUTHORISED = "AUTHORISED"
_DECISION_REJECTED = "REJECTED"
_DECISION_OVERRODE_APPROVAL = "OVERRODE_AGENT_APPROVAL"
_DECISION_OVERRODE_FLAG = "OVERRODE_AGENT_FLAG"


class DepartmentHeadGradingService:
    """
    Read + write surface for the DH grading workflow. Every method
    auth-checks against ``CourseManagementOfficer.role ==
    DEPARTMENT_HEAD`` before reading or mutating anything.
    """

    def __init__(
        self,
        db: AsyncSession,
        *,
        grading_agent: Optional[GradingMonitorAgent] = None,
    ) -> None:
        self.db = db
        self._agent = grading_agent

    def _resolve_agent(self) -> GradingMonitorAgent:
        """Same lazy-build pattern the instructor service uses."""
        if self._agent is None:
            from app.ai.llm_client import build_default_llm_client
            self._agent = GradingMonitorAgent(
                llm_client=build_default_llm_client(),
            )
        return self._agent

    # ── Auth gate ───────────────────────────────────────────────

    async def _resolve_dh_or_403(self, user_id: uuid.UUID) -> User:
        """
        The caller must be either:
          - a ``CourseManagementOfficer`` row with
            ``role == DEPARTMENT_HEAD``, or
          - a ``UserRole.ADMIN`` user.

        Mirrors the role check used by
        ``InstructorService._require_dh_or_admin`` in Track A so the
        permission model is consistent across modules.
        """
        user = (
            await self.db.execute(
                select(User).where(User.id == user_id)
            )
        ).scalar_one_or_none()
        if user is None:
            raise DepartmentHeadRoleRequiredError()
        if user.role == UserRole.ADMIN:
            return user
        officer = (
            await self.db.execute(
                select(CourseManagementOfficer).where(
                    CourseManagementOfficer.user_id == user_id,
                    CourseManagementOfficer.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()
        if officer is None or officer.role != OfficerRole.DEPARTMENT_HEAD:
            raise DepartmentHeadRoleRequiredError()
        return user

    # ── Queue filter options ────────────────────────────────────

    async def list_queue_departments(
        self,
        *,
        user_id: uuid.UUID,
        term_id: Optional[uuid.UUID] = None,
    ) -> list[QueueDepartmentOption]:
        """
        Distinct departments that currently have at least one batch
        awaiting a DH decision (status SUBMITTED or FLAGGED), with a
        pending count each. Backs the queue's department-filter
        dropdown so the frontend never has to type a free-text
        department string. Optionally scoped to a term so the
        dropdown matches the term the DH is viewing.

        Sorted alphabetically by department for a stable dropdown.
        """
        await self._resolve_dh_or_403(user_id)

        stmt = (
            select(
                Section.department,
                func.count(GradeBatch.id),
            )
            .join(Section, Section.id == GradeBatch.section_id)
            .where(
                GradeBatch.status.in_({
                    GradeSubmissionStatus.SUBMITTED,
                    GradeSubmissionStatus.FLAGGED,
                }),
                GradeBatch.is_deleted == False,  # noqa: E712
            )
            .group_by(Section.department)
            .order_by(Section.department.asc())
        )
        if term_id is not None:
            stmt = stmt.where(GradeBatch.term_id == term_id)

        rows = (await self.db.execute(stmt)).all()
        return [
            QueueDepartmentOption(department=dept, pending_count=count)
            for dept, count in rows
        ]

    # ── Queue ───────────────────────────────────────────────────

    async def list_queue(
        self,
        *,
        user_id: uuid.UUID,
        term_id: Optional[uuid.UUID] = None,
        department: Optional[str] = None,
    ) -> list[DepartmentHeadQueueEntry]:
        """
        Every SUBMITTED or FLAGGED batch, sorted by ``submitted_at``
        ascending (oldest first). Optional filters narrow the queue
        by term or department.

        Convenience fields: ``latest_agent_verdict`` and
        ``flag_count`` from the most recent agent review, plus
        ``roster_total`` so the DH can size up the batch at a glance.
        """
        await self._resolve_dh_or_403(user_id)

        stmt = (
            select(GradeBatch, Section, Course, AcademicTerm, Instructor, User)
            .join(Section, Section.id == GradeBatch.section_id)
            .join(Course, Course.id == GradeBatch.course_id)
            .join(AcademicTerm, AcademicTerm.id == GradeBatch.term_id)
            .join(Instructor, Instructor.id == GradeBatch.instructor_id)
            .join(User, User.id == Instructor.user_id)
            .where(
                GradeBatch.status.in_({
                    GradeSubmissionStatus.SUBMITTED,
                    GradeSubmissionStatus.FLAGGED,
                }),
                GradeBatch.is_deleted == False,  # noqa: E712
            )
            .order_by(GradeBatch.submitted_at.asc())
        )
        if term_id is not None:
            stmt = stmt.where(GradeBatch.term_id == term_id)
        if department is not None:
            stmt = stmt.where(Section.department == department)
        rows = (await self.db.execute(stmt)).all()

        entries: list[DepartmentHeadQueueEntry] = []
        for batch, section, course, term, _instructor, user in rows:
            latest_review = await self._latest_review(batch.id)
            roster = await derive_section_course_roster(
                self.db, section_id=batch.section_id,
                course_id=batch.course_id,
            )
            entries.append(DepartmentHeadQueueEntry(
                batch_id=batch.id,
                section_id=section.id,
                section_code=section.section_code,
                section_department=section.department,
                section_semester=section.semester,
                course_id=course.id,
                course_code=course.code,
                course_title=course.title,
                term_id=term.id,
                term_name=term.term_name,
                instructor_id=batch.instructor_id,
                instructor_name=f"{user.first_name} {user.last_name}".strip(),
                status=batch.status,
                iteration_count=batch.iteration_count,
                submitted_at=batch.submitted_at,
                latest_agent_verdict=(
                    latest_review.verdict if latest_review else None
                ),
                flag_count=(
                    len(latest_review.flags or [])
                    if latest_review else 0
                ),
                has_instructor_justification=(
                    batch.instructor_justification is not None
                ),
                roster_total=len(roster),
            ))
        return entries

    # ── Review packet ───────────────────────────────────────────

    async def get_review_packet(
        self,
        *,
        user_id: uuid.UUID,
        batch_id: uuid.UUID,
    ) -> GradeBatchReviewPacketResponse:
        """
        Full DH view of one batch: the instructor's batch payload
        (breakdown + score matrix + per-student letters) joined to
        every agent run and every prior DH decision.
        """
        await self._resolve_dh_or_403(user_id)

        # Reuse the instructor service's batch-builder so the score
        # matrix and per-student letters come out identical to what
        # the instructor sees — there's no risk of two views drifting.
        from app.modules.course.grading.service import InstructorGradingService

        batch = await self._resolve_batch_or_404(batch_id)
        section = (await self.db.execute(
            select(Section).where(Section.id == batch.section_id)
        )).scalar_one()
        course = (await self.db.execute(
            select(Course).where(Course.id == batch.course_id)
        )).scalar_one()
        breakdown = (await self.db.execute(
            select(AssessmentBreakdown).where(
                AssessmentBreakdown.id == batch.breakdown_id,
            )
        )).scalar_one()

        helper = InstructorGradingService(self.db)
        batch_view = await helper._build_batch_response(
            batch=batch, breakdown=breakdown,
            section=section, course=course,
        )

        # Per-student computed letters — only meaningful when the
        # batch has been submitted (status != DRAFT). For SUBMITTED
        # / FLAGGED / AUTHORISED / REJECTED the cells are complete
        # and the math is stable.
        per_student_grades = await self._compute_per_student_for_dh(
            batch=batch, breakdown=breakdown, course=course,
        )

        # Agent review history (newest first).
        agent_review_rows = (await self.db.execute(
            select(GradeAgentReview)
            .where(GradeAgentReview.batch_id == batch.id)
            .order_by(
                GradeAgentReview.iteration.desc(),
                GradeAgentReview.created_at.desc(),
            )
        )).scalars().all()

        # Decision history (newest first).
        decision_rows = (await self.db.execute(
            select(GradeAuthorisationDecision)
            .where(GradeAuthorisationDecision.batch_id == batch.id)
            .order_by(GradeAuthorisationDecision.decision_at.desc())
        )).scalars().all()

        return GradeBatchReviewPacketResponse(
            batch=batch_view,
            per_student_grades=per_student_grades,
            agent_reviews=[
                GradeAgentReviewResponse(
                    id=r.id, iteration=r.iteration, verdict=r.verdict,
                    flags=r.flags or [],
                    llm_reasoning=r.llm_reasoning,
                    tool_findings=r.tool_findings or {},
                    agent_id=r.agent_id, created_at=r.created_at,
                )
                for r in agent_review_rows
            ],
            decisions=[
                GradeAuthorisationDecisionResponse(
                    id=d.id, iteration=d.iteration, decision=d.decision,
                    department_head_id=d.department_head_id,
                    decision_at=d.decision_at,
                    justification=d.justification,
                )
                for d in decision_rows
            ],
        )

    # ── Terminal decisions ──────────────────────────────────────

    async def authorise(
        self,
        *,
        user_id: uuid.UUID,
        batch_id: uuid.UUID,
        justification: Optional[str] = None,
    ) -> DepartmentHeadDecisionResponse:
        """
        Mark the batch official:
          - SUBMITTED → AUTHORISED   (decision = AUTHORISED;
            justification optional)
          - FLAGGED   → AUTHORISED   (decision = OVERRODE_AGENT_FLAG;
            justification REQUIRED — DH is overruling the agent)

        Sets every ``Grade`` row's status to AUTHORISED so Track A's
        CGPA calculator picks them up, and stamps ``authorised_by_id``
        / ``authorised_at`` on each.
        """
        user = await self._resolve_dh_or_403(user_id)
        batch = await self._resolve_batch_or_404(batch_id)
        self._guard_batch_reviewable(batch)

        if batch.status == GradeSubmissionStatus.FLAGGED:
            decision = _DECISION_OVERRODE_FLAG
            if not justification or not justification.strip():
                raise JustificationRequiredError(decision)
        else:
            decision = _DECISION_AUTHORISED

        now = datetime.now(timezone.utc)

        # Flip Grade rows to AUTHORISED.
        grades = (await self.db.execute(
            select(Grade).where(
                Grade.course_id == batch.course_id,
                Grade.term_id == batch.term_id,
                Grade.section_id == batch.section_id,
            )
        )).scalars().all()
        for g in grades:
            g.status = GradeSubmissionStatus.AUTHORISED
            g.authorised_by_id = user_id
            g.authorised_at = now

        # Flip the batch status.
        batch.status = GradeSubmissionStatus.AUTHORISED

        decision_row = GradeAuthorisationDecision(
            batch_id=batch.id,
            iteration=batch.iteration_count,
            decision=decision,
            department_head_id=user_id,
            decision_at=now,
            justification=(justification.strip() if justification else None),
        )
        self.db.add(decision_row)
        await self.db.flush()
        await self.db.commit()

        return DepartmentHeadDecisionResponse(
            batch_id=batch.id,
            new_status=batch.status,
            decision=decision,
            decision_id=decision_row.id,
            decision_at=decision_row.decision_at,
            department_head_id=user_id,
        )

    async def reject(
        self,
        *,
        user_id: uuid.UUID,
        batch_id: uuid.UUID,
        justification: str,
    ) -> DepartmentHeadDecisionResponse:
        """
        Send the batch back to the instructor:
          - FLAGGED   → REJECTED   (decision = REJECTED)
          - SUBMITTED → REJECTED   (decision = OVERRODE_AGENT_APPROVAL)

        Both paths REQUIRE a written justification — the instructor
        will read it. Grade rows are flipped to REJECTED so Track A's
        CGPA never sees them in this state; the next submit (after
        the instructor reopens) puts them back at SUBMITTED.
        """
        await self._resolve_dh_or_403(user_id)
        batch = await self._resolve_batch_or_404(batch_id)
        self._guard_batch_reviewable(batch)

        if not justification or not justification.strip():
            raise JustificationRequiredError("REJECTED")

        decision = (
            _DECISION_REJECTED
            if batch.status == GradeSubmissionStatus.FLAGGED
            else _DECISION_OVERRODE_APPROVAL
        )

        now = datetime.now(timezone.utc)

        # Flip Grade rows to REJECTED. We don't clear the numeric/
        # letter — they're useful audit context until the instructor
        # re-submits and overwrites them.
        grades = (await self.db.execute(
            select(Grade).where(
                Grade.course_id == batch.course_id,
                Grade.term_id == batch.term_id,
                Grade.section_id == batch.section_id,
            )
        )).scalars().all()
        for g in grades:
            g.status = GradeSubmissionStatus.REJECTED

        batch.status = GradeSubmissionStatus.REJECTED

        decision_row = GradeAuthorisationDecision(
            batch_id=batch.id,
            iteration=batch.iteration_count,
            decision=decision,
            department_head_id=user_id,
            decision_at=now,
            justification=justification.strip(),
        )
        self.db.add(decision_row)
        await self.db.flush()
        await self.db.commit()

        return DepartmentHeadDecisionResponse(
            batch_id=batch.id,
            new_status=batch.status,
            decision=decision,
            decision_id=decision_row.id,
            decision_at=decision_row.decision_at,
            department_head_id=user_id,
        )

    async def rerun_agent(
        self,
        *,
        user_id: uuid.UUID,
        batch_id: uuid.UUID,
    ) -> AgentRerunResponse:
        """
        Re-invoke the GradingMonitorAgent on a batch — typically used
        when the previous run landed PENDING because the LLM was
        unavailable. Writes a fresh ``grade_agent_reviews`` row and
        transitions the batch:

          - APPROVE → status SUBMITTED (if currently FLAGGED, flips
                       back; if SUBMITTED-with-PENDING, stays).
          - FLAG    → status FLAGGED.
          - PENDING → status unchanged.

        No DH decision row is written by this method — that comes
        from authorise / reject explicitly.
        """
        await self._resolve_dh_or_403(user_id)
        batch = await self._resolve_batch_or_404(batch_id)
        if batch.status not in {
            GradeSubmissionStatus.SUBMITTED,
            GradeSubmissionStatus.FLAGGED,
        }:
            raise GradeBatchNotReviewableError(batch.status.value)

        # Run the agent. Persistence + transition mirror the submit
        # path so the audit trail is consistent.
        from app.modules.course.grading.service import InstructorGradingService
        helper = InstructorGradingService(self.db, grading_agent=self._agent)
        agent = self._resolve_agent()
        helper._agent = agent  # share the cached instance
        review = await agent.review_batch(db=self.db, batch_id=batch.id)
        await helper._persist_agent_review(
            batch=batch, review=review, agent=agent,
        )

        if review.verdict == "APPROVE":
            batch.status = GradeSubmissionStatus.SUBMITTED
        elif review.verdict == "FLAG":
            batch.status = GradeSubmissionStatus.FLAGGED
        # PENDING → leave status as-is.

        await self.db.flush()
        await self.db.commit()

        return AgentRerunResponse(
            batch_id=batch.id,
            new_status=batch.status,
            iteration=batch.iteration_count,
            agent_verdict=review.verdict,
            agent_flags=review.flags,
            agent_reasoning=review.reasoning,
        )

    # ── Helpers ─────────────────────────────────────────────────

    async def _resolve_batch_or_404(
        self, batch_id: uuid.UUID,
    ) -> GradeBatch:
        batch = (await self.db.execute(
            select(GradeBatch).where(
                GradeBatch.id == batch_id,
                GradeBatch.is_deleted == False,  # noqa: E712
            )
        )).scalar_one_or_none()
        if batch is None:
            raise EntityNotFoundError("GradeBatch", str(batch_id))
        return batch

    @staticmethod
    def _guard_batch_reviewable(batch: GradeBatch) -> None:
        """Only SUBMITTED or FLAGGED batches accept a DH decision."""
        if batch.status not in {
            GradeSubmissionStatus.SUBMITTED,
            GradeSubmissionStatus.FLAGGED,
        }:
            raise GradeBatchNotReviewableError(batch.status.value)

    async def _latest_review(
        self, batch_id: uuid.UUID,
    ) -> Optional[GradeAgentReview]:
        return (await self.db.execute(
            select(GradeAgentReview)
            .where(GradeAgentReview.batch_id == batch_id)
            .order_by(
                GradeAgentReview.iteration.desc(),
                GradeAgentReview.created_at.desc(),
            )
            .limit(1)
        )).scalar_one_or_none()

    async def _compute_per_student_for_dh(
        self,
        *,
        batch: GradeBatch,
        breakdown: AssessmentBreakdown,
        course: Course,
    ) -> list[SubmittedGradeRow]:
        """Same math the submit path used — re-derived for the packet."""
        roster = await derive_section_course_roster(
            self.db, section_id=batch.section_id,
            course_id=batch.course_id,
        )
        components = sorted(
            (await self.db.execute(
                select(AssessmentComponent).where(
                    AssessmentComponent.breakdown_id == breakdown.id,
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
        out: list[SubmittedGradeRow] = []
        for member in roster:
            weighted = 0.0
            complete = True
            for comp in components:
                raw = score_by_cell.get((member.student_id, comp.id))
                if raw is None:
                    complete = False
                    break
                weighted += (raw / comp.max_score) * comp.weight
            if not complete:
                continue
            weighted = max(0.0, min(100.0, weighted))
            out.append(SubmittedGradeRow(
                student_id=member.student_id,
                student_number=member.student_number,
                full_name=member.full_name,
                numeric_score=round(weighted, 4),
                letter_grade=letter_for_numeric(weighted),
            ))
        return out
