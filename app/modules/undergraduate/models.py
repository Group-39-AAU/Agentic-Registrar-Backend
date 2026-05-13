"""
Undergraduate Admission module — all SQLAlchemy models.

Contains:
    - UndergraduateApplication (core aggregate, SoftDeleteBase)
    - ApplicationDocument (supporting entity, SoftDeleteBase)
    - ApplicationStatusHistory (immutable ledger, Base)
    - RegistrarDecision (immutable decision record, Base)
"""

import uuid
from datetime import date
from typing import Optional

from sqlalchemy import (
    JSON, Boolean, Date, Float, ForeignKey, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, SoftDeleteBase
from app.shared.enums import (
    ApplicationStatus, DecisionType, DocumentType,
    PaymentStatus, SponsorshipType, StreamType, VerificationStatus,
)


# ── Core Aggregate ────────────────────────────────────────────


class UndergraduateAdmissionTerm(SoftDeleteBase):
    """Configurable undergraduate admission intake term."""
    __tablename__ = "undergraduate_admission_terms"

    term_name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class UndergraduateApplication(SoftDeleteBase):
    """
    Central system-of-record for a student's undergraduate admission request.
    """
    __tablename__ = "undergraduate_applications"

    applicant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), index=True, nullable=False
    )

    # ── Sponsorship & Stream ──
    sponsorship_type: Mapped[SponsorshipType] = mapped_column(nullable=False)
    stream: Mapped[StreamType] = mapped_column(nullable=False)

    # ── Grade 12 admission number (used to query MoE database) ──
    admission_number: Mapped[str] = mapped_column(String(50), nullable=False)

    # ── Program choices (self-sponsored only, nullable for government) ──
    program_choice_1_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("academic_programs.id"), nullable=True
    )
    program_choice_2_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("academic_programs.id"), nullable=True
    )
    program_choice_3_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("academic_programs.id"), nullable=True
    )

    admission_term_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("undergraduate_admission_terms.id"), index=True, nullable=False
    )
    current_status: Mapped[ApplicationStatus] = mapped_column(
        nullable=False, default=ApplicationStatus.DRAFT, index=True
    )
    final_decision: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )

    # ── Payment ──
    payment_status: Mapped[PaymentStatus] = mapped_column(
        nullable=False, default=PaymentStatus.PENDING
    )
    payment_reference: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )

    remarks: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    extra_data: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        server_default="{}",
        nullable=False,
    )

    # ── Relationships (lazy="selectin" per architecture blueprint) ──
    documents: Mapped[list["ApplicationDocument"]] = relationship(
        back_populates="application", lazy="selectin",
        foreign_keys="ApplicationDocument.application_id",
    )
    status_history: Mapped[list["ApplicationStatusHistory"]] = relationship(
        back_populates="application", lazy="selectin",
        order_by="ApplicationStatusHistory.created_at.asc()",
    )
    decision: Mapped[Optional["RegistrarDecision"]] = relationship(
        back_populates="application", lazy="selectin", uselist=False
    )
    admission_term: Mapped["UndergraduateAdmissionTerm"] = relationship(lazy="selectin")

    # ── Constraints ──
    __table_args__ = (
        UniqueConstraint(
            "applicant_id", "admission_term_id",
            name="uq_one_app_per_term",
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
        back_populates="documents", lazy="selectin",
        foreign_keys=[application_id],
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
