from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared_kernel.db import Base, TimestampMixin

from ..enums import ApplicationStatus, VerificationOutcome


class ApplicantProfile(Base, TimestampMixin):
    __tablename__ = "applicant_profiles"
    __table_args__ = {"schema": "undergrad_admission"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    phone_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    national_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)

    applications: Mapped[list["UndergraduateApplication"]] = relationship(
        back_populates="applicant",
        lazy="selectin",
    )


class UndergraduateApplication(Base, TimestampMixin):
    __tablename__ = "applications"
    __table_args__ = {"schema": "undergrad_admission"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    applicant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("undergrad_admission.applicant_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    program_code: Mapped[str] = mapped_column(String(64), nullable=False)
    intake_year: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ApplicationStatus] = mapped_column(
        String(64),
        nullable=False,
        default=ApplicationStatus.DRAFT,
    )
    cumulative_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ranking_position: Mapped[int | None] = mapped_column(Integer, nullable=True)

    applicant: Mapped[ApplicantProfile] = relationship(back_populates="applications")
    documents: Mapped[list["ApplicationDocumentReference"]] = relationship(
        back_populates="application",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    verifications: Mapped[list["VerificationResult"]] = relationship(
        back_populates="application",
        lazy="selectin",
        cascade="all, delete-orphan",
    )


class ApplicationDocumentReference(Base, TimestampMixin):
    __tablename__ = "application_documents"
    __table_args__ = (
        UniqueConstraint(
            "application_id",
            "document_id",
            name="uq_undergrad_documents_application_document",
        ),
        {"schema": "undergrad_admission"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("undergrad_admission.applications.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    document_type: Mapped[str] = mapped_column(String(64), nullable=False)

    application: Mapped[UndergraduateApplication] = relationship(back_populates="documents")


class VerificationResult(Base, TimestampMixin):
    __tablename__ = "verification_results"
    __table_args__ = {"schema": "undergrad_admission"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("undergrad_admission.applications.id", ondelete="CASCADE"),
        nullable=False,
    )
    step: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[VerificationOutcome] = mapped_column(
        String(32),
        nullable=False,
        default=VerificationOutcome.PENDING,
    )
    details: Mapped[str | None] = mapped_column(String(512), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    application: Mapped[UndergraduateApplication] = relationship(back_populates="verifications")


class RankingResult(Base, TimestampMixin):
    __tablename__ = "ranking_results"
    __table_args__ = {"schema": "undergrad_admission"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("undergrad_admission.applications.id", ondelete="CASCADE"),
        nullable=False,
    )
    cumulative_score: Mapped[float] = mapped_column(Float, nullable=False)
    rank_position: Mapped[int] = mapped_column(Integer, nullable=False)


class HumanReviewCheckpoint(Base, TimestampMixin):
    __tablename__ = "human_review_checkpoints"
    __table_args__ = {"schema": "undergrad_admission"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("undergrad_admission.applications.id", ondelete="CASCADE"),
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    resolved: Mapped[bool] = mapped_column(default=False)

