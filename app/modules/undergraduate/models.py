"""
Undergraduate Admission module — all SQLAlchemy models.

Contains:
    - UndergraduateApplication (core aggregate, SoftDeleteBase)
    - ApplicationDocument (supporting entity, SoftDeleteBase)
    - ApplicationStatusHistory (immutable ledger, Base)
    - RegistrarDecision (immutable decision record, Base)
"""

import uuid
from typing import Optional

from sqlalchemy import (
    Boolean, Float, ForeignKey, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, SoftDeleteBase
from app.shared.enums import (
    ApplicationStatus, DecisionType, DocumentType, VerificationStatus,
)


# ── Core Aggregate ────────────────────────────────────────────


class UndergraduateApplication(SoftDeleteBase):
    """
    Central system-of-record for a student's undergraduate admission request.
    """
    __tablename__ = "undergraduate_applications"

    applicant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), index=True, nullable=False
    )
    program_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("academic_programs.id"), nullable=False
    )
    admission_term: Mapped[str] = mapped_column(
        String(50), index=True, nullable=False
    )
    current_status: Mapped[ApplicationStatus] = mapped_column(
        nullable=False, default=ApplicationStatus.DRAFT, index=True
    )
    final_decision: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )
    remarks: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    extra_data: Mapped[dict] = mapped_column(
        JSONB, server_default="{}", nullable=False
    )

    # ── Relationships (lazy="selectin" per architecture blueprint) ──
    documents: Mapped[list["ApplicationDocument"]] = relationship(
        back_populates="application", lazy="selectin"
    )
    status_history: Mapped[list["ApplicationStatusHistory"]] = relationship(
        back_populates="application", lazy="selectin",
        order_by="ApplicationStatusHistory.created_at.asc()",
    )
    decision: Mapped[Optional["RegistrarDecision"]] = relationship(
        back_populates="application", lazy="selectin", uselist=False
    )

    # ── Constraints ──
    __table_args__ = (
        UniqueConstraint(
            "applicant_id", "program_id", "admission_term",
            name="uq_one_app_per_program_per_term",
        ),
    )


# ── Supporting Entity ─────────────────────────────────────────


class ApplicationDocument(SoftDeleteBase):
    """
    Official artifacts submitted by the applicant (transcripts, IDs, etc.).
    """
    __tablename__ = "application_documents"

    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("undergraduate_applications.id"),
        index=True, nullable=False,
    )
    document_type: Mapped[DocumentType] = mapped_column(nullable=False)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    verification_status: Mapped[VerificationStatus] = mapped_column(
        nullable=False, default=VerificationStatus.PENDING, index=True
    )
    verified_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )

    # ── Relationships ──
    application: Mapped["UndergraduateApplication"] = relationship(
        back_populates="documents", lazy="selectin"
    )


# ── Immutable Ledger ──────────────────────────────────────────


class ApplicationStatusHistory(Base):
    """
    Immutable audit trail of every status transition.
    Inherits from Base — NO soft delete, NO update, NO delete.
    """
    __tablename__ = "application_status_history"

    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("undergraduate_applications.id"),
        index=True, nullable=False,
    )
    previous_status: Mapped[Optional[ApplicationStatus]] = mapped_column(
        nullable=True
    )
    new_status: Mapped[ApplicationStatus] = mapped_column(nullable=False)
    changed_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    trigger_reason: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )

    # ── Relationships ──
    application: Mapped["UndergraduateApplication"] = relationship(
        back_populates="status_history", lazy="selectin"
    )


# ── Human Decision Record ────────────────────────────────────


class RegistrarDecision(Base):
    """
    The official, legally binding human decision record.
    Inherits from Base — immutable once written.
    """
    __tablename__ = "registrar_decisions"

    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("undergraduate_applications.id"),
        unique=True, nullable=False,
    )
    reviewer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), index=True, nullable=False
    )
    human_decision: Mapped[DecisionType] = mapped_column(nullable=False)
    justification_remarks: Mapped[str] = mapped_column(Text, nullable=False)
    override_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Relationships ──
    application: Mapped["UndergraduateApplication"] = relationship(
        back_populates="decision", lazy="selectin"
    )
