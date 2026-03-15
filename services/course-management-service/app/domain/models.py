from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared_kernel.db import Base, TimestampMixin


class Registration(Base, TimestampMixin):
    __tablename__ = "registrations"
    __table_args__ = {"schema": "course_management"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    student_id: Mapped[str] = mapped_column(String(64), nullable=False)
    term: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    total_credits: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    items: Mapped[list["RegistrationItem"]] = relationship(
        back_populates="registration",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class RegistrationItem(Base, TimestampMixin):
    __tablename__ = "registration_items"
    __table_args__ = {"schema": "course_management"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    registration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("course_management.registrations.id", ondelete="CASCADE"),
        nullable=False,
    )
    course_code: Mapped[str] = mapped_column(String(32), nullable=False)
    credits: Mapped[float] = mapped_column(Float, nullable=False)

    registration: Mapped[Registration] = relationship(back_populates="items")


class AddDropRequest(Base, TimestampMixin):
    __tablename__ = "add_drop_requests"
    __table_args__ = {"schema": "course_management"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    registration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("course_management.registrations.id", ondelete="CASCADE"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)  # "add" or "drop"
    course_code: Mapped[str] = mapped_column(String(32), nullable=False)


class GradeSubmission(Base, TimestampMixin):
    __tablename__ = "grade_submissions"
    __table_args__ = {"schema": "course_management"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    section_id: Mapped[str] = mapped_column(String(64), nullable=False)
    submitted_by: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")


class GradeEntry(Base, TimestampMixin):
    __tablename__ = "grade_entries"
    __table_args__ = {"schema": "course_management"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    submission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("course_management.grade_submissions.id", ondelete="CASCADE"),
        nullable=False,
    )
    student_id: Mapped[str] = mapped_column(String(64), nullable=False)
    course_code: Mapped[str] = mapped_column(String(32), nullable=False)
    grade: Mapped[float] = mapped_column(Float, nullable=False)
    credits: Mapped[float] = mapped_column(Float, nullable=False)


class AcademicStanding(Base, TimestampMixin):
    __tablename__ = "academic_standings"
    __table_args__ = {"schema": "course_management"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    student_id: Mapped[str] = mapped_column(String(64), nullable=False)
    term: Mapped[str] = mapped_column(String(32), nullable=False)
    gpa: Mapped[float] = mapped_column(Float, nullable=False)
    cgpa: Mapped[float] = mapped_column(Float, nullable=False)
    standing: Mapped[str] = mapped_column(String(32), nullable=False)
    authorized: Mapped[bool] = mapped_column(default=False)


class AcademicRecord(Base, TimestampMixin):
    __tablename__ = "academic_records"
    __table_args__ = {"schema": "course_management"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    student_id: Mapped[str] = mapped_column(String(64), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[str] = mapped_column(String(2048), nullable=False)


class ManualExceptionCase(Base, TimestampMixin):
    __tablename__ = "manual_exception_cases"
    __table_args__ = {"schema": "course_management"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    registration_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

