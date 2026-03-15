from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared_kernel.db import Base, TimestampMixin

from .enums import ApplicationStatus, VerificationOutcome


class GraduateApplicantProfile(Base, TimestampMixin):
    __tablename__ = "graduate_applicant_profiles"
    __table_args__ = {"schema": "graduate_admission"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    phone_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    national_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    applications: Mapped[list["GraduateApplication"]] = relationship(
        back_populates="applicant",
        lazy="selectin",
    )


class GraduateApplication(Base, TimestampMixin):
    __tablename__ = "graduate_applications"
    __table_args__ = {"schema": "graduate_admission"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    applicant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("graduate_admission.graduate_applicant_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    program_code: Mapped[str] = mapped_column(String(64), nullable=False)
    intake_year: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ApplicationStatus] = mapped_column(
        String(64),
        nullable=False,
        default=ApplicationStatus.DRAFT,
    )
    gat_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    transcript_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    cumulative_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ranking_position: Mapped[int | None] = mapped_column(Integer, nullable=True)

    applicant: Mapped[GraduateApplicantProfile] = relationship(back_populates="applications")
    documents: Mapped[list["GraduateDocumentReference"]] = relationship(
        back_populates="application",
        lazy="selectin",
        cascade="all, delete-orphan",
    )


class GraduateDocumentReference(Base, TimestampMixin):
    __tablename__ = "graduate_document_references"
    __table_args__ = {"schema": "graduate_admission"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("graduate_admission.graduate_applications.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    document_type: Mapped[str] = mapped_column(String(64), nullable=False)

    application: Mapped[GraduateApplication] = relationship(back_populates="documents")


class DepartmentEvaluationResult(Base, TimestampMixin):
    __tablename__ = "department_evaluation_results"
    __table_args__ = {"schema": "graduate_admission"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("graduate_admission.graduate_applications.id", ondelete="CASCADE"),
        nullable=False,
    )
    evaluator: Mapped[str] = mapped_column(String(255), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    comments: Mapped[str | None] = mapped_column(String(512), nullable=True)


class DepartmentReviewCheckpoint(Base, TimestampMixin):
    __tablename__ = "department_review_checkpoints"
    __table_args__ = {"schema": "graduate_admission"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("graduate_admission.graduate_applications.id", ondelete="CASCADE"),
        nullable=False,
    )
    resolved: Mapped[bool] = mapped_column(default=False)


class RegistrarReviewCheckpoint(Base, TimestampMixin):
    __tablename__ = "registrar_review_checkpoints"
    __table_args__ = {"schema": "graduate_admission"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("graduate_admission.graduate_applications.id", ondelete="CASCADE"),
        nullable=False,
    )
    resolved: Mapped[bool] = mapped_column(default=False)


class EnrollmentResult(Base, TimestampMixin):
    __tablename__ = "enrollment_results"
    __table_args__ = {"schema": "graduate_admission"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("graduate_admission.graduate_applications.id", ondelete="CASCADE"),
        nullable=False,
    )
    enrolled: Mapped[bool] = mapped_column(default=False)
    enrolled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

