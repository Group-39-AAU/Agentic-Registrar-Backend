"""
Track C (Academic Standing) — SQLAlchemy models.

Two new tables drive the standing-determination workflow:

  ``AcademicStanding``        — one row per (student, term) carrying
                                the SGPA / CGPA / proposed-status
                                math, plus the DH-authorisation
                                fields. Unique on the pair so a
                                student has exactly one standing per
                                term.

  ``AcademicStandingHistory`` — append-only audit trail of every
                                transition (proposed → authorised,
                                authorised → overridden, etc.).
                                Reconstructable past-status answer
                                to "what was this student's standing
                                in 2024?".

The math is derived from authorised ``Grade`` rows in Track A's
``grades`` table; this module stores the *outcome* of running the
rules engine, not the underlying grades. Re-computing is idempotent —
the agent upserts the standing row with a fresh proposal.

Source-of-truth: AAU Senate Legislation Articles 90 & 91.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer, String,
    Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, SoftDeleteBase
from app.shared.enums import AcademicStatusType


class AcademicStanding(SoftDeleteBase):
    """
    Per-student-per-term academic standing record.

    Lifecycle:

      proposed_status set by agent (PR C2)
        → final_status = NULL, requires_review possibly true (I/NG)
      authorised_by_id + authorised_at set by DH (PR C2)
        → final_status = proposed_status (or override)
      override_reason set when DH disagrees with the proposal

    Computation context fields (snapshotted at compute time so the
    standing is reproducible even if the underlying student
    progression later changes):

      * ``term_credit_hours`` — sum of credit hours of authorised
        grades that count toward CGPA in *this* term.
      * ``cumulative_credit_hours`` — sum across all completed terms.
      * ``f_count_term`` / ``f_credit_total_term`` — F-grade
        statistics used by Article 91.4 / 91.7.2 rule branches.
      * ``is_first_semester`` / ``is_first_year`` — needed by Article
        91.5 / 91.6 / 91.7.5 / 91.7.6 (stricter rules for new admits).
      * ``prior_status`` + ``consecutive_warning_count`` — drive
        Article 91.7.1 "consecutive probation → dismissal".

    UniqueConstraint(student_id, term_id) enforces "one standing per
    student per term"; re-running compute upserts rather than
    inserting a fresh row.
    """

    __tablename__ = "academic_standings"

    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("students.id"),
        nullable=False,
        index=True,
    )
    term_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academic_terms.id"),
        nullable=False,
        index=True,
    )
    # Denormalised so the officer queue can filter without joining
    # through Registration to Section. Populated at compute time from
    # the student's cohort section (or the student's own department
    # column when no section is assigned).
    department: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True,
    )

    # ── GPA math (snapshot at compute time) ──
    sgpa: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cgpa: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    term_credit_hours: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    cumulative_credit_hours: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )

    # ── Article 91 evaluation context ──
    f_count_term: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    f_credit_total_term: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    is_first_semester: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
    )
    is_first_year: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
    )
    prior_status: Mapped[Optional[AcademicStatusType]] = mapped_column(
        nullable=True,
    )
    consecutive_warning_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )

    # ── Status + authorisation ──
    proposed_status: Mapped[AcademicStatusType] = mapped_column(
        nullable=False, index=True,
    )
    final_status: Mapped[Optional[AcademicStatusType]] = mapped_column(
        nullable=True, index=True,
    )
    # True when the term contains any I / NG mark — Article 90.7
    # routes these through officer review rather than computing a
    # numeric verdict. Mirrors the SDS ``handleEdgeCase`` path.
    requires_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True,
    )

    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    computed_by_agent_id: Mapped[Optional[str]] = mapped_column(
        String(120), nullable=True,
    )
    authorised_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True,
    )
    authorised_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    override_reason: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
    )

    student: Mapped["Student"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        lazy="selectin",
    )
    term: Mapped["AcademicTerm"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint(
            "student_id", "term_id",
            name="uq_standing_per_student_term",
        ),
        CheckConstraint(
            "sgpa IS NULL OR (sgpa >= 0 AND sgpa <= 4.0)",
            name="ck_standing_sgpa_range",
        ),
        CheckConstraint(
            "cgpa IS NULL OR (cgpa >= 0 AND cgpa <= 4.0)",
            name="ck_standing_cgpa_range",
        ),
        CheckConstraint(
            "term_credit_hours >= 0",
            name="ck_standing_term_credit_hours_non_negative",
        ),
        CheckConstraint(
            "cumulative_credit_hours >= 0",
            name="ck_standing_cumulative_credit_hours_non_negative",
        ),
        CheckConstraint(
            "f_count_term >= 0",
            name="ck_standing_f_count_non_negative",
        ),
        CheckConstraint(
            "consecutive_warning_count >= 0",
            name="ck_standing_consecutive_warning_non_negative",
        ),
    )


class AcademicStandingHistory(Base):
    """
    Append-only audit trail of every state transition on an
    :class:`AcademicStanding` row. One row per event.

    ``event`` discriminator values (matched at write time in service
    code, enforced by a CHECK constraint):

      ``PROPOSED``    — agent wrote the initial / re-computed proposal
      ``AUTHORISED``  — DH accepted the proposal
      ``OVERRIDDEN``  — DH set ``final_status`` different from
                        ``proposed_status`` (justification required)
      ``RE_COMPUTED`` — agent re-ran on an already-authorised standing
                        (rare; e.g. a late-corrected grade)

    ``changed_by_id`` is null for agent events (PROPOSED, RE_COMPUTED)
    and is the User who acted for DH events.
    """

    __tablename__ = "academic_standing_history"

    standing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academic_standings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event: Mapped[str] = mapped_column(String(20), nullable=False)
    previous_status: Mapped[Optional[AcademicStatusType]] = mapped_column(
        nullable=True,
    )
    new_status: Mapped[AcademicStatusType] = mapped_column(nullable=False)
    changed_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True,
    )
    agent_id: Mapped[Optional[str]] = mapped_column(
        String(120), nullable=True,
    )
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    standing: Mapped["AcademicStanding"] = relationship(lazy="selectin")

    __table_args__ = (
        CheckConstraint(
            "event IN ('PROPOSED', 'AUTHORISED', 'OVERRIDDEN', 'RE_COMPUTED')",
            name="ck_standing_history_event",
        ),
    )
