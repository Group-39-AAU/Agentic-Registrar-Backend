"""
Course Management — SQLAlchemy models.

The module follows the same layered pattern as undergraduate admission:

    models.py     — entities defined here
    schemas.py    — Pydantic request/response shapes
    repository.py — async CRUD
    service.py    — business logic + audit logging
    router.py     — API endpoints

Phase 0 entities (this file) are deliberately catalog-only:
AcademicTerm, Course, CoursePrerequisite, CourseOffering, Section,
Student, Instructor, InstructorAssignment, CourseManagementOfficer.

Workflow tables (Registration, Grades, Schedules, AcademicStanding,
ExceptionQueue, AcademicRecord) belong to Tracks A/B/C and land in
their respective PRs after this Phase 0 baseline merges.

Source-of-truth: SDS §3.1.3 Figure 5 (class diagram) and §5.3
Tables 55–84 (detailed design).
"""

import uuid
from datetime import date, datetime, time
from typing import Optional

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Integer,
    String, Text, Time, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, SoftDeleteBase
from app.shared.enums import (
    AddDropAction, AddDropRequestStatus, EnrollmentStatus, OfficerRole,
    RegistrationStatus, RiskStatus, ScheduleConflictStatus,
    ScheduleConflictType, SponsorshipType,
)


# ── Academic Calendar ────────────────────────────────────────────


class AcademicTerm(SoftDeleteBase):
    """
    A configurable academic term (semester) governing the Course
    Management lifecycle. The ``is_open`` flag is the gate the officer
    flips for the SRS Course-FR-01 "Course Registration Portal" use case
    — students cannot register against a term while it is closed.

    Tied to SDS state diagram (Figure 39) by being the parent of the
    Registration_Open / Add_Drop_Window calendar windows.
    """

    __tablename__ = "academic_terms"

    term_name: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_open: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


# ── Course Catalog ───────────────────────────────────────────────


class Course(SoftDeleteBase):
    """
    A catalog course (e.g. CS101 "Introduction to Programming").

    Independent of any academic term; per-term offerings are recorded
    on :class:`CourseOffering`. The ``credit_hours`` value drives the
    22-ECTS ceiling and 12-ECTS floor enforced by the Curriculum
    Compliance Agent (SDS Tables 65, 80).
    """

    __tablename__ = "courses"

    code: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    credit_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    semester: Mapped[int] = mapped_column(Integer, nullable=False)
    department: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "credit_hours BETWEEN 1 AND 12",
            name="ck_courses_credit_hours_range",
        ),
        CheckConstraint(
            "semester BETWEEN 1 AND 12",
            name="ck_courses_semester_range",
        ),
    )


class CoursePrerequisite(Base):
    """
    Self-referential mapping linking a course to its prerequisite
    courses. Powers ``CurriculumComplianceAgent.verifyPrerequisites``
    (SDS Table 66) — a registration is allowed only when every linked
    prerequisite has been passed with grade >= F.

    Inherits from :class:`Base` (no soft delete) because curriculum
    versions are append-only — a removed prerequisite stays in history.
    """

    __tablename__ = "course_prerequisites"

    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("courses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    prerequisite_course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("courses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    course: Mapped["Course"] = relationship(
        foreign_keys=[course_id], lazy="selectin"
    )
    prerequisite_course: Mapped["Course"] = relationship(
        foreign_keys=[prerequisite_course_id], lazy="selectin"
    )

    __table_args__ = (
        UniqueConstraint(
            "course_id", "prerequisite_course_id",
            name="uq_course_prereq_pair",
        ),
        CheckConstraint(
            "course_id <> prerequisite_course_id",
            name="ck_course_prereq_not_self",
        ),
    )


class CourseOffering(SoftDeleteBase):
    """
    A specific course offered in a specific academic term, with its
    own seat capacity and number of sections. Sits between
    :class:`Course` (term-independent) and :class:`Section` (concrete
    timetable slot) so the catalog is reusable across terms.

    Realises the SRS Course-FR-04 "Section & Schedule Generation"
    use case — the Academic Scheduling Agent groups registered
    students into the offering's sections honouring the recorded
    capacity and section_count.
    """

    __tablename__ = "course_offerings"

    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("courses.id"),
        nullable=False,
        index=True,
    )
    term_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academic_terms.id"),
        nullable=False,
        index=True,
    )
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    section_count: Mapped[int] = mapped_column(Integer, nullable=False)

    course: Mapped["Course"] = relationship(lazy="selectin")
    term: Mapped["AcademicTerm"] = relationship(lazy="selectin")

    __table_args__ = (
        UniqueConstraint(
            "course_id", "term_id",
            name="uq_course_offering_per_term",
        ),
        CheckConstraint("capacity > 0", name="ck_offering_capacity_positive"),
        CheckConstraint(
            "section_count > 0",
            name="ck_offering_section_count_positive",
        ),
    )


class Section(SoftDeleteBase):
    """
    A class **cohort** for a single (term, department, semester) tuple:
    a group of students at the same point in their program who attend
    every course of that semester together in the same room.

    Section codes are globally unique within a term (A, B, C, … across
    every department/semester). Capacity is the room capacity; the
    Academic Scheduling Agent splits a (term, department, semester)
    student population into as many sections as the largest eligible
    room can absorb.

    Per-class meetings (which course meets when, with which instructor,
    in which fixed slot of the section's weekly schedule) live on
    :class:`ClassScheduleSlot`.
    """

    __tablename__ = "sections"

    term_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academic_terms.id"),
        nullable=False, index=True,
    )
    department: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True,
    )
    semester: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
    )
    section_code: Mapped[str] = mapped_column(String(10), nullable=False)
    room: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    enrolled_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )

    term: Mapped["AcademicTerm"] = relationship(lazy="selectin")

    __table_args__ = (
        UniqueConstraint(
            "term_id", "section_code",
            name="uq_section_code_per_term",
        ),
        CheckConstraint(
            "semester BETWEEN 1 AND 12",
            name="ck_sections_semester_range",
        ),
        CheckConstraint("capacity > 0", name="ck_section_capacity_positive"),
        CheckConstraint(
            "enrolled_count >= 0 AND enrolled_count <= capacity",
            name="ck_section_enrolled_within_capacity",
        ),
    )


class ClassScheduleSlot(Base):
    """
    One weekly meeting of a course inside a Section's schedule. Each
    course attended by a section gets ``course.credit_hours`` hours of
    these slots per week, so a 3-credit course → three 1-hour slots
    (or one 3-hour block, depending on what the agent picks). The
    section's room is fixed across all its slots.

    Append-only: regenerating the schedule deletes and re-inserts.
    """

    __tablename__ = "class_schedule_slots"

    section_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sections.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("courses.id"),
        nullable=False, index=True,
    )
    instructor_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("instructors.id"),
        nullable=True, index=True,
    )
    day_of_week: Mapped[str] = mapped_column(
        String(3), nullable=False,
        comment="MON / TUE / WED / THU / FRI",
    )
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)

    section: Mapped["Section"] = relationship(lazy="selectin")
    course: Mapped["Course"] = relationship(lazy="selectin")

    __table_args__ = (
        UniqueConstraint(
            "section_id", "day_of_week", "start_time",
            name="uq_section_slot_per_day_start",
        ),
        CheckConstraint(
            "day_of_week IN ('MON','TUE','WED','THU','FRI')",
            name="ck_schedule_slot_day_of_week",
        ),
        CheckConstraint(
            "end_time > start_time",
            name="ck_schedule_slot_end_after_start",
        ),
    )


# ── People ───────────────────────────────────────────────────────


class Student(SoftDeleteBase):
    """
    Course-Management profile for an admitted student. References the
    generic ``users`` row created during admission and adds the
    academic attributes from SDS Table 56:

        - ``student_id``      : AAU format ``UGR/XXXX/YY``
        - ``full_name``       : denormalised for display
        - ``current_semester``: integer in [1, 12]
        - ``enrollment_status``: ACTIVE / DISMISSED / WITHDRAWN /
                                 GRADUATED — distinct from per-term
                                 :class:`AcademicStatusType`

    ``academicHistory`` from the SDS is intentionally a *derived* view
    (read from grade rows + status history) and is not persisted on
    this entity.
    """

    __tablename__ = "students"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    student_id: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False, index=True
    )
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    current_semester: Mapped[int] = mapped_column(Integer, nullable=False)
    # Denormalised from Enrollment.department at onboarding time so
    # the curriculum filter doesn't need a cross-module join. Nullable
    # for backward compat with rows seeded before this column existed;
    # OnboardingService always populates it on new rows.
    department: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, index=True,
    )
    # Denormalised from UndergraduateApplication.sponsorship_type at
    # onboarding time. The student doesn't choose this per-registration
    # — it's determined by the admission process. Nullable for legacy
    # rows; OnboardingService always populates it on new rows.
    sponsorship_type: Mapped[Optional[SponsorshipType]] = mapped_column(
        nullable=True,
    )
    enrollment_status: Mapped[EnrollmentStatus] = mapped_column(
        nullable=False, default=EnrollmentStatus.ACTIVE, index=True
    )

    __table_args__ = (
        CheckConstraint(
            "current_semester BETWEEN 1 AND 12",
            name="ck_students_current_semester_range",
        ),
    )


class Instructor(SoftDeleteBase):
    """
    Course-Management profile for a teaching staff member per SDS
    Tables 58–59. References the generic ``users`` row 1-to-1 and
    adds the staff identifier (``STAFF/XXXX/YY``) and owning
    department.

    The set of courses an instructor is assigned to teach in a given
    term is recorded on :class:`InstructorAssignment`, not as a list
    column on this row, so assignments can be inspected per-term and
    audited independently.
    """

    __tablename__ = "instructors"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    instructor_id: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False, index=True
    )
    department: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True
    )


class InstructorAssignment(Base):
    """
    Per-term assignment linking an :class:`Instructor` to a
    :class:`Course`. Materialises the ``assignedCourses`` collection
    from SDS Table 58 in a normalised way so the AcademicScheduling
    Agent can reason about an instructor's load per term.

    Append-only (inherits :class:`Base`) so de-assignments are
    historically preserved rather than overwritten.
    """

    __tablename__ = "instructor_assignments"

    instructor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("instructors.id"),
        nullable=False,
        index=True,
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("courses.id"),
        nullable=False,
        index=True,
    )
    term_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academic_terms.id"),
        nullable=False,
        index=True,
    )

    instructor: Mapped["Instructor"] = relationship(lazy="selectin")
    course: Mapped["Course"] = relationship(lazy="selectin")
    term: Mapped["AcademicTerm"] = relationship(lazy="selectin")

    __table_args__ = (
        UniqueConstraint(
            "instructor_id", "course_id", "term_id",
            name="uq_instructor_course_term",
        ),
    )


class CourseManagementOfficer(SoftDeleteBase):
    """
    Course-Management profile for the human officer per SDS Tables
    61–62. References the generic ``users`` row 1-to-1 and adds:

        - ``staff_id``           : ``REG/XXXX/YY`` format
        - ``role``               : REGISTRAR_OFFICER or DEPARTMENT_HEAD
        - ``authorization_level``: integer in [1, 5]; gates which
                                    sensitivity tier of records the
                                    officer may modify

    Per SRS §3.5 inverse requirement and SDS Table 62, only an officer
    with ``role == DEPARTMENT_HEAD`` may grant a manual prerequisite
    override on the Curriculum Compliance Agent's verdict.
    """

    __tablename__ = "course_management_officers"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    staff_id: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False, index=True
    )
    role: Mapped[OfficerRole] = mapped_column(
        nullable=False, default=OfficerRole.REGISTRAR_OFFICER, index=True
    )
    authorization_level: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "authorization_level BETWEEN 1 AND 5",
            name="ck_officer_authorization_level_range",
        ),
    )


# ── Track A — Registration Workflow ──────────────────────────────


class Registration(SoftDeleteBase):
    """
    Per-student-per-term registration aggregate. Drives the SDS
    Figure 39 state machine via :class:`RegistrationStatus`.

    The ``payment_reference`` is the opaque identifier returned by
    PayMock and is the field the Curriculum Compliance Agent's
    ``checkPaymentStatus`` uses to confirm payment is settled before
    the registration can transition to ``REGISTERED``.

    UniqueConstraint(student_id, term_id) enforces SRS Course-FR-01's
    "one registration per student per term" rule — the re-registration
    guard listed in the Track A implementation checklist.
    """

    __tablename__ = "registrations"

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
    status: Mapped[RegistrationStatus] = mapped_column(
        nullable=False,
        default=RegistrationStatus.REGISTRATION_OPEN,
        index=True,
    )
    sponsorship_type: Mapped[SponsorshipType] = mapped_column(nullable=False)
    payment_reference: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    finalised_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Cohort assignment — null until the AcademicSchedulingAgent has
    # placed this student into a Section for the term. Once set, every
    # course on the registration is attended in this Section's room
    # at the times listed in ClassScheduleSlot rows.
    section_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sections.id"),
        nullable=True, index=True,
    )

    student: Mapped["Student"] = relationship(lazy="selectin")
    term: Mapped["AcademicTerm"] = relationship(lazy="selectin")
    section: Mapped[Optional["Section"]] = relationship(lazy="selectin")
    courses: Mapped[list["RegistrationCourse"]] = relationship(
        back_populates="registration", lazy="selectin",
    )
    status_history: Mapped[list["RegistrationStatusHistory"]] = relationship(
        back_populates="registration", lazy="selectin",
        order_by="RegistrationStatusHistory.created_at.asc()",
    )

    __table_args__ = (
        UniqueConstraint(
            "student_id", "term_id",
            name="uq_one_registration_per_student_term",
        ),
    )


class RegistrationCourse(Base):
    """
    Junction recording which courses a registration includes. Section
    assignment is on :class:`Registration` (one cohort per term); this
    table just records "this student takes this course this term".

    Append-only (inherits :class:`Base`) — drops are surfaced as
    AddDropRequest rows rather than mutations of this table, so the
    registration history is reconstructable.
    """

    __tablename__ = "registration_courses"

    registration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("registrations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("courses.id"),
        nullable=False,
        index=True,
    )
    is_dropped: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    registration: Mapped["Registration"] = relationship(
        back_populates="courses", lazy="selectin",
    )
    course: Mapped["Course"] = relationship(lazy="selectin")

    __table_args__ = (
        UniqueConstraint(
            "registration_id", "course_id",
            name="uq_registration_course_pair",
        ),
    )


class RegistrationStatusHistory(Base):
    """
    Immutable audit trail of every Registration status transition.
    Mirrors the undergraduate ``ApplicationStatusHistory`` pattern.

    ``changed_by_id`` is null for agent-driven transitions; the
    ``agent_id`` column is set instead so observability dashboards
    can attribute the transition to a specific agent instance.
    """

    __tablename__ = "registration_status_history"

    registration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("registrations.id"),
        nullable=False,
        index=True,
    )
    previous_status: Mapped[Optional[RegistrationStatus]] = mapped_column(
        nullable=True
    )
    new_status: Mapped[RegistrationStatus] = mapped_column(nullable=False)
    changed_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    agent_id: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )
    trigger_reason: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )

    registration: Mapped["Registration"] = relationship(
        back_populates="status_history", lazy="selectin",
    )


# ── Track A — Add/Drop Workflow ──────────────────────────────────


class AddDropRequest(SoftDeleteBase):
    """
    A student-initiated post-registration change request handled by
    the EnrollmentAdjustmentAgent.

    ``deadline_snapshot`` is captured at submit time so late-window
    rule changes do not retroactively break records (Track A
    implementation checklist invariant).

    ``override_by_id`` and ``override_justification`` are populated
    only when an officer overrides a DENIED request — for prerequisite
    overrides the calling code MUST verify the officer's role is
    ``DEPARTMENT_HEAD`` per SRS §3.5.
    """

    __tablename__ = "add_drop_requests"

    registration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("registrations.id"),
        nullable=False,
        index=True,
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("courses.id"),
        nullable=False,
        index=True,
    )
    action: Mapped[AddDropAction] = mapped_column(nullable=False)
    deadline_snapshot: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[AddDropRequestStatus] = mapped_column(
        nullable=False,
        default=AddDropRequestStatus.PENDING,
        index=True,
    )
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    override_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    override_justification: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )

    registration: Mapped["Registration"] = relationship(lazy="selectin")
    course: Mapped["Course"] = relationship(lazy="selectin")


# ── Track A — Advisory ───────────────────────────────────────────


class AdvisoryRecommendation(Base):
    """
    Persisted snapshot of an AcademicAdvisoryAgent verdict (SDS
    Table 69). The advisory content (proposed_courses,
    recommended_courses, gap_analysis, risk_status, risk_explanation)
    is the immutable snapshot — once the agent has spoken, those
    fields don't change. The officer-review fields are mutable and
    track whether a HIGH-risk verdict has been reviewed.

    ``proposed_courses``       — list of course UUIDs the student asked about
    ``recommended_courses``    — agent's prioritised next-step suggestions
    ``gap_analysis``           — completed-vs-remaining structured payload
    ``requires_officer_review`` — agent flagged this for HITL review
    ``reviewed_by_id``         — officer who closed the review
    ``reviewed_at`` / ``review_notes`` — review-closure metadata
    """

    __tablename__ = "advisory_recommendations"

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
    risk_status: Mapped[RiskStatus] = mapped_column(nullable=False, index=True)
    risk_explanation: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_courses: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list
    )
    recommended_courses: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list
    )
    gap_analysis: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict
    )

    # Officer-review fields (HITL escalation gate for HIGH risk).
    requires_officer_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True,
    )
    reviewed_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True,
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    review_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    student: Mapped["Student"] = relationship(lazy="selectin")
    term: Mapped["AcademicTerm"] = relationship(lazy="selectin")


# ── Track A — Prerequisite Override Audit ────────────────────────


class PrerequisiteOverride(Base):
    """
    Immutable record of a Department-Head-granted bypass of the
    Curriculum Compliance Agent's prerequisite verdict. Required by
    SRS §3.5 inverse requirement and by SDS Table 62 (only an
    officer with ``role == DEPARTMENT_HEAD`` may grant the override;
    enforcement lives in the service layer).

    A registration may accumulate multiple PrerequisiteOverride rows
    (one per overridden course), so the trail is fully reconstructable
    when the override is later questioned.
    """

    __tablename__ = "prerequisite_overrides"

    registration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("registrations.id"),
        nullable=False,
        index=True,
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("courses.id"),
        nullable=False,
        index=True,
    )
    granted_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    justification: Mapped[str] = mapped_column(Text, nullable=False)

    registration: Mapped["Registration"] = relationship(
        foreign_keys=[registration_id], lazy="selectin",
    )
    course: Mapped["Course"] = relationship(
        foreign_keys=[course_id], lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint(
            "registration_id", "course_id",
            name="uq_prerequisite_override_per_course",
        ),
    )


# ── Track A — Scheduling Conflict Report ─────────────────────────


class ScheduleConflict(Base):
    """
    Records a scheduling clash detected by the AcademicSchedulingAgent
    that its auto-resolution heuristics could not fix on their own.
    Used by the human-visible conflict report listed in the Track A
    implementation checklist.

    The agent writes one row per detected clash; the officer reviewing
    the report flips ``status`` to RESOLVED_BY_OFFICER (with a
    ``resolution_note``) once they have applied a manual fix.
    """

    __tablename__ = "schedule_conflicts"

    term_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academic_terms.id"),
        nullable=False,
        index=True,
    )
    department: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True
    )
    conflict_type: Mapped[ScheduleConflictType] = mapped_column(
        nullable=False, index=True
    )
    section_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sections.id"), nullable=True,
    )
    other_section_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sections.id"), nullable=True,
    )
    instructor_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("instructors.id"), nullable=True,
    )
    time_slot: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )
    room: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    detected_by_agent_id: Mapped[str] = mapped_column(
        String(100), nullable=False
    )
    status: Mapped[ScheduleConflictStatus] = mapped_column(
        nullable=False,
        default=ScheduleConflictStatus.OPEN,
        index=True,
    )
    resolution_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolved_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True,
    )

    term: Mapped["AcademicTerm"] = relationship(lazy="selectin")
    section: Mapped[Optional["Section"]] = relationship(
        foreign_keys=[section_id], lazy="selectin",
    )
    other_section: Mapped[Optional["Section"]] = relationship(
        foreign_keys=[other_section_id], lazy="selectin",
    )
