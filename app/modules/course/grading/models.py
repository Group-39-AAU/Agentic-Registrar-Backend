"""
Track B (Grading) — SQLAlchemy models.

Four new tables drive the PR 2 workflow (breakdown editor + draft
score entry + submit). They live in the grading submodule rather than
``app/modules/course/models.py`` to keep Track A's already-large
models file from continuing to grow.

  ``AssessmentBreakdown``      — the instructor's component plan for
                                 one (section, course) pair. Locks
                                 to edits the moment the first
                                 component score is saved, so a
                                 breakdown change after grades exist
                                 cannot retroactively re-weight them.
  ``AssessmentComponent``      — one row of the breakdown
                                 (e.g. "Mid-term", weight=20%).
                                 The sum of ``weight`` across the
                                 components of a breakdown must equal
                                 exactly 100; the invariant is
                                 enforced in the service layer, not
                                 by a CHECK (Postgres can't express
                                 a sum across rows in CHECK).
  ``GradeBatch``               — the per-(section, course) workflow
                                 object the instructor submits and
                                 the agent / department head review.
                                 Status uses the existing
                                 ``GradeSubmissionStatus`` enum so
                                 the DRAFT → SUBMITTED → FLAGGED →
                                 AUTHORISED / REJECTED lifecycle is
                                 shared with Track A's ``Grade``.
  ``StudentComponentScore``    — one raw score per (batch, student,
                                 component). Nullable while DRAFT;
                                 submit refuses any missing cell.

The Track A ``Grade`` row is the *final* per-student outcome: the
weighted numeric is computed at submit time and stored on the existing
``grades`` table. This module *creates* / *updates* Grade rows but
does not redefine the table.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON, CheckConstraint, DateTime, Enum, Float, ForeignKey, Integer, String,
    Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, SoftDeleteBase
from app.shared.enums import GradeSubmissionStatus


# JSONB on Postgres for indexability; JSON falls back on SQLite (tests).
_JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")


# ── Breakdown editor ─────────────────────────────────────────────


class AssessmentBreakdown(SoftDeleteBase):
    """
    The instructor's per-(section, course) plan for how the final
    grade is composed. One active breakdown per (section, course).

    Re-uploading the same breakdown after components exist is allowed
    until ``locked_at`` is set; once any ``StudentComponentScore``
    has been saved against a component of this breakdown, the
    breakdown is locked (``locked_at`` is set on first score-save)
    and any further write returns 409. Editing then requires the
    instructor to delete all entered scores first.
    """

    __tablename__ = "assessment_breakdowns"

    section_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sections.id"),
        nullable=False, index=True,
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("courses.id"),
        nullable=False, index=True,
    )
    instructor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("instructors.id"),
        nullable=False, index=True,
    )
    term_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("academic_terms.id"),
        nullable=False, index=True,
    )
    # Bumps each time the breakdown is overwritten before locking,
    # so the UI can show "v3" if the instructor iterated.
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1,
    )
    # Set the moment the first StudentComponentScore is saved.
    # Once non-null, the breakdown is immutable.
    locked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False,
    )

    components: Mapped[list["AssessmentComponent"]] = relationship(
        back_populates="breakdown",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="AssessmentComponent.order_index",
    )

    __table_args__ = (
        UniqueConstraint(
            "section_id", "course_id",
            name="uq_breakdown_per_section_course",
        ),
    )


class AssessmentComponent(Base):
    """
    One weighted row of an :class:`AssessmentBreakdown`. The sum of
    weights across a breakdown's components must equal exactly 100;
    that invariant is enforced in the service layer (Postgres CHECK
    can't sum across rows) and validated again at submit time.
    """

    __tablename__ = "assessment_components"

    breakdown_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assessment_breakdowns.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Percentage of the final grade contributed by this component.
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    # The denominator for the raw score the instructor enters
    # (e.g. "Quiz out of 10" → 10). At the schema layer this defaults
    # to the component's ``weight`` when the caller omits it, so an
    # instructor who grades on the same scale as the weight can skip
    # it entirely. The DB-level default is a defensive fallback only.
    max_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=100.0,
    )
    # Stable display order — UI lists components Mid-term then Final,
    # not in INSERT order, regardless of when they were edited.
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)

    breakdown: Mapped["AssessmentBreakdown"] = relationship(
        back_populates="components", lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint(
            "breakdown_id", "name",
            name="uq_component_name_per_breakdown",
        ),
        CheckConstraint(
            "weight > 0 AND weight <= 100",
            name="ck_component_weight_range",
        ),
        CheckConstraint(
            "max_score > 0",
            name="ck_component_max_score_positive",
        ),
    )


# ── Grade-entry batch ────────────────────────────────────────────


class GradeBatch(SoftDeleteBase):
    """
    The per-(section, course) grading-workflow object. One live batch
    per pair; status drives the DRAFT → SUBMITTED → FLAGGED →
    AUTHORISED / REJECTED lifecycle. Re-grading after a REJECTED
    decision lands by moving the same row back to DRAFT and bumping
    ``iteration_count``, not by creating a new batch.

    ``instructor_justification`` is the optional remark the instructor
    attaches when a SUBMITTED batch is FLAGGED — used by the
    department-head in PR 4 to weigh the flag.
    """

    __tablename__ = "grade_batches"

    section_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sections.id"),
        nullable=False, index=True,
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("courses.id"),
        nullable=False, index=True,
    )
    instructor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("instructors.id"),
        nullable=False, index=True,
    )
    term_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("academic_terms.id"),
        nullable=False, index=True,
    )
    breakdown_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assessment_breakdowns.id"),
        nullable=False, index=True,
    )

    status: Mapped[GradeSubmissionStatus] = mapped_column(
        Enum(
            GradeSubmissionStatus,
            name="gradesubmissionstatus",
            values_callable=lambda enum_cls: [m.value for m in enum_cls],
            create_type=False,
        ),
        nullable=False,
        default=GradeSubmissionStatus.DRAFT,
        index=True,
    )
    submitted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    submitted_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True,
    )
    instructor_justification: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
    )
    iteration_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1,
    )

    breakdown: Mapped["AssessmentBreakdown"] = relationship(lazy="selectin")

    __table_args__ = (
        UniqueConstraint(
            "section_id", "course_id",
            name="uq_grade_batch_per_section_course",
        ),
        CheckConstraint(
            "iteration_count >= 1",
            name="ck_grade_batch_iteration_positive",
        ),
    )


class StudentComponentScore(Base):
    """
    Per-(batch, student, component) raw score. Nullable while the
    batch is in DRAFT so the instructor can save partial work. At
    SUBMIT time, the service refuses any (student, component) pair
    with a NULL score.

    ``score`` is the raw entry against ``AssessmentComponent.max_score``;
    the weighted contribution is ``(score / max_score) * weight`` —
    computed in code at submit time, not stored, so a breakdown edit
    (before lock) doesn't leave inconsistent denormalised totals.
    """

    __tablename__ = "student_component_scores"

    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grade_batches.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id"),
        nullable=False, index=True,
    )
    component_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assessment_components.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "batch_id", "student_id", "component_id",
            name="uq_score_per_batch_student_component",
        ),
        CheckConstraint(
            "score IS NULL OR score >= 0",
            name="ck_score_non_negative",
        ),
    )


# ── Agent audit trail ────────────────────────────────────────────


class GradeAgentReview(Base):
    """
    One row per ``GradingMonitorAgent`` run on a batch. Append-only:
    re-running the agent (after the instructor edits scores or
    attaches a justification) writes a NEW row, never updates an
    existing one. The instructor's UI shows the latest; the
    department head's UI (PR 4) shows the full iteration history.

    ``verdict`` is the agent's APPROVE / FLAG decision. ``PENDING``
    is the special value written when the LLM call failed — the
    deterministic ``tool_findings`` are still saved so the DH can
    see what the agent was looking at, but the verdict and
    ``llm_reasoning`` are deferred to a manual re-run.

    ``tool_findings`` is the deterministic context dictionary the
    tool layer compiled (class statistics, per-component stats,
    outliers, distribution, missing-cell check, breakdown integrity,
    roster summary). The LLM saw exactly this payload when it
    decided.

    ``flags`` is the structured list of concerns the LLM (or the
    PENDING fallback) raised — each entry is
    ``{type, severity, message, affected_student_ids?}``.
    """

    __tablename__ = "grade_agent_reviews"

    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grade_batches.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    iteration: Mapped[int] = mapped_column(Integer, nullable=False)
    verdict: Mapped[str] = mapped_column(String(20), nullable=False)
    tool_findings: Mapped[dict] = mapped_column(
        _JSON_TYPE, nullable=False,
    )
    llm_reasoning: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
    )
    flags: Mapped[list] = mapped_column(
        _JSON_TYPE, nullable=False, default=list,
    )
    agent_id: Mapped[str] = mapped_column(String(120), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "verdict IN ('APPROVE', 'FLAG', 'PENDING')",
            name="ck_grade_agent_review_verdict",
        ),
        CheckConstraint(
            "iteration >= 1",
            name="ck_grade_agent_review_iteration_positive",
        ),
    )


# ── Department-head authorisation decisions ─────────────────────


class GradeAuthorisationDecision(Base):
    """
    One immutable row per terminal department-head decision on a
    batch. Append-only: an "overturn" writes a NEW row, never edits
    an existing one. The (batch, decision-time) pair is the audit
    trail; the latest row decides current status.

    ``decision`` records both the outcome (authorised / rejected) and
    whether the DH agreed with or overrode the agent's prior
    verdict, so the full chain is reconstructable:

      AUTHORISED              — agent APPROVED + DH agrees
      REJECTED                — agent FLAGGED + DH agrees → instructor redo
      OVERRODE_AGENT_APPROVAL — agent APPROVED + DH rejects → instructor redo
      OVERRODE_AGENT_FLAG     — agent FLAGGED + DH authorises → grades official

    ``justification`` is required for every decision except
    ``AUTHORISED`` (accepting a clean APPROVE needs no written
    reason). Service layer enforces this.
    """

    __tablename__ = "grade_authorisation_decisions"

    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grade_batches.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    iteration: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[str] = mapped_column(String(40), nullable=False)
    department_head_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"),
        nullable=False, index=True,
    )
    decision_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    justification: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "decision IN ('AUTHORISED', 'REJECTED', "
            "'OVERRODE_AGENT_APPROVAL', 'OVERRODE_AGENT_FLAG')",
            name="ck_authorisation_decision_value",
        ),
        CheckConstraint(
            "iteration >= 1",
            name="ck_authorisation_iteration_positive",
        ),
    )
