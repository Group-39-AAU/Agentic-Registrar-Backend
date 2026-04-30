"""add course management phase 0

Phase 0 of the Course Management module (SDS §3.1.3 + §5.3):

  - 10 new postgres ENUM types
  - 9 new tables (catalog + people)

The migration creates tables in FK-dependency order:

    academic_terms
    courses
    course_prerequisites    (-> courses)
    course_offerings        (-> courses, academic_terms)
    instructors             (-> users)
    sections                (-> course_offerings, instructors)
    students                (-> users)
    course_management_officers (-> users)
    instructor_assignments  (-> instructors, courses, academic_terms)

Workflow tables (registrations, grade batches, schedule artefacts,
academic-status records, exception queue, generated documents) are
deliberately *not* created here — they belong to Tracks A/B/C and
land in their own per-track migrations.

Revision ID: c0a3e0b00001
Revises: 7c1a2b3d4e5f
Create Date: 2026-04-26 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "c0a3e0b00001"
down_revision: Union[str, None] = "7c1a2b3d4e5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ── Enum value lists (must stay in sync with app.shared.enums) ───

_REGISTRATION_STATUS = (
    "REGISTRATION_OPEN", "ADVISOR_REVIEW", "CHECKING_PREREQUISITES",
    "CHECKING_PAYMENT", "PAYMENT_HOLD", "VALIDATION_SUCCESS",
    "REGISTERED", "ADD_DROP_WINDOW", "CANCELLED",
)
_GRADE_LETTER = (
    "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D", "F", "I", "NG",
)
_GRADE_SUBMISSION_STATUS = (
    "DRAFT", "SUBMITTED", "FLAGGED", "AUTHORISED", "REJECTED",
)
_ACADEMIC_STATUS_TYPE = (
    "PROMOTED", "WARNING", "DISTINCTION", "DISMISSED", "INCOMPLETE",
)
_ENROLLMENT_STATUS = (
    "ACTIVE", "DISMISSED", "WITHDRAWN", "GRADUATED",
)
_EXCEPTION_STATUS = ("OPEN", "IN_REVIEW", "RESOLVED", "ESCALATED")
_AGENT_STATUS = ("IDLE", "BUSY", "WAITING_HUMAN", "ERROR")
_ADD_DROP_ACTION = ("ADD", "DROP")
_OFFICER_ROLE = ("REGISTRAR_OFFICER", "DEPARTMENT_HEAD")
_RISK_STATUS = ("LOW", "MEDIUM", "HIGH")


def upgrade() -> None:
    # NOTE: We intentionally do NOT pre-create the eight enum types that
    # are only referenced by Track A/B/C tables. SQLAlchemy's
    # ``create_type=False`` flag is unreliable in alembic
    # ``op.create_table`` contexts — the table-create still fires the
    # enum's ``before_create`` listener, which calls ``CREATE TYPE`` and
    # collides with the pre-created instance. Each track creates the
    # enums it needs, defining each enum object once and reusing the
    # same instance across its tables (SQLAlchemy memoises by identity).
    #
    # The two enums actually used by Phase-0 columns (enrollmentstatus,
    # officerrole) are auto-created by their respective table columns.
    bind = op.get_bind()

    # ── 2. academic_terms ────────────────────────────────────────
    op.create_table(
        "academic_terms",
        sa.Column("term_name", sa.String(length=100), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("is_open", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index(op.f("ix_academic_terms_term_name"), "academic_terms", ["term_name"], unique=True)

    # ── 3. courses ───────────────────────────────────────────────
    op.create_table(
        "courses",
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("credit_hours", sa.Integer(), nullable=False),
        sa.Column("semester", sa.Integer(), nullable=False),
        sa.Column("department", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("credit_hours BETWEEN 1 AND 12", name="ck_courses_credit_hours_range"),
        sa.CheckConstraint("semester BETWEEN 1 AND 12", name="ck_courses_semester_range"),
    )
    op.create_index(op.f("ix_courses_code"), "courses", ["code"], unique=True)
    op.create_index(op.f("ix_courses_department"), "courses", ["department"], unique=False)

    # ── 4. course_prerequisites ──────────────────────────────────
    op.create_table(
        "course_prerequisites",
        sa.Column("course_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("prerequisite_course_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["prerequisite_course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("course_id", "prerequisite_course_id", name="uq_course_prereq_pair"),
        sa.CheckConstraint("course_id <> prerequisite_course_id", name="ck_course_prereq_not_self"),
    )
    op.create_index(op.f("ix_course_prerequisites_course_id"), "course_prerequisites", ["course_id"], unique=False)
    op.create_index(op.f("ix_course_prerequisites_prerequisite_course_id"), "course_prerequisites", ["prerequisite_course_id"], unique=False)

    # ── 5. course_offerings ──────────────────────────────────────
    op.create_table(
        "course_offerings",
        sa.Column("course_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("term_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("section_count", sa.Integer(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"]),
        sa.ForeignKeyConstraint(["term_id"], ["academic_terms.id"]),
        sa.UniqueConstraint("course_id", "term_id", name="uq_course_offering_per_term"),
        sa.CheckConstraint("capacity > 0", name="ck_offering_capacity_positive"),
        sa.CheckConstraint("section_count > 0", name="ck_offering_section_count_positive"),
    )
    op.create_index(op.f("ix_course_offerings_course_id"), "course_offerings", ["course_id"], unique=False)
    op.create_index(op.f("ix_course_offerings_term_id"), "course_offerings", ["term_id"], unique=False)

    # ── 6. instructors (must come before sections) ───────────────
    op.create_table(
        "instructors",
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("instructor_id", sa.String(length=20), nullable=False),
        sa.Column("department", sa.String(length=100), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.UniqueConstraint("user_id", name="uq_instructors_user_id"),
        sa.UniqueConstraint("instructor_id", name="uq_instructors_instructor_id"),
    )
    op.create_index(op.f("ix_instructors_user_id"), "instructors", ["user_id"], unique=False)
    op.create_index(op.f("ix_instructors_instructor_id"), "instructors", ["instructor_id"], unique=False)
    op.create_index(op.f("ix_instructors_department"), "instructors", ["department"], unique=False)

    # ── 7. sections ──────────────────────────────────────────────
    op.create_table(
        "sections",
        sa.Column("offering_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("section_code", sa.String(length=10), nullable=False),
        sa.Column("room", sa.String(length=50), nullable=True),
        sa.Column("time_slot", sa.String(length=100), nullable=True),
        sa.Column("instructor_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("enrolled_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["offering_id"], ["course_offerings.id"]),
        sa.ForeignKeyConstraint(["instructor_id"], ["instructors.id"]),
        sa.UniqueConstraint("offering_id", "section_code", name="uq_section_code_per_offering"),
        sa.CheckConstraint("capacity > 0", name="ck_section_capacity_positive"),
        sa.CheckConstraint("enrolled_count >= 0 AND enrolled_count <= capacity", name="ck_section_enrolled_within_capacity"),
    )
    op.create_index(op.f("ix_sections_offering_id"), "sections", ["offering_id"], unique=False)
    op.create_index(op.f("ix_sections_instructor_id"), "sections", ["instructor_id"], unique=False)

    # ── 8. students ──────────────────────────────────────────────
    op.create_table(
        "students",
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("student_id", sa.String(length=20), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("current_semester", sa.Integer(), nullable=False),
        sa.Column(
            "enrollment_status",
            sa.Enum(*_ENROLLMENT_STATUS, name="enrollmentstatus", create_type=False),
            nullable=False,
            server_default="ACTIVE",
        ),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.UniqueConstraint("user_id", name="uq_students_user_id"),
        sa.UniqueConstraint("student_id", name="uq_students_student_id"),
        sa.CheckConstraint("current_semester BETWEEN 1 AND 12", name="ck_students_current_semester_range"),
    )
    # The enrollmentstatus enum was not pre-created above so it is
    # created here. Subsequent tables that reference it must use
    # create_type=False.
    sa.Enum(*_ENROLLMENT_STATUS, name="enrollmentstatus").create(bind, checkfirst=True)
    op.create_index(op.f("ix_students_user_id"), "students", ["user_id"], unique=False)
    op.create_index(op.f("ix_students_student_id"), "students", ["student_id"], unique=False)
    op.create_index(op.f("ix_students_enrollment_status"), "students", ["enrollment_status"], unique=False)

    # ── 9. course_management_officers ────────────────────────────
    op.create_table(
        "course_management_officers",
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("staff_id", sa.String(length=20), nullable=False),
        sa.Column(
            "role",
            sa.Enum(*_OFFICER_ROLE, name="officerrole"),
            nullable=False,
            server_default="REGISTRAR_OFFICER",
        ),
        sa.Column("authorization_level", sa.Integer(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.UniqueConstraint("user_id", name="uq_officers_user_id"),
        sa.UniqueConstraint("staff_id", name="uq_officers_staff_id"),
        sa.CheckConstraint("authorization_level BETWEEN 1 AND 5", name="ck_officer_authorization_level_range"),
    )
    op.create_index(op.f("ix_course_management_officers_user_id"), "course_management_officers", ["user_id"], unique=False)
    op.create_index(op.f("ix_course_management_officers_staff_id"), "course_management_officers", ["staff_id"], unique=False)
    op.create_index(op.f("ix_course_management_officers_role"), "course_management_officers", ["role"], unique=False)

    # ── 10. instructor_assignments ───────────────────────────────
    op.create_table(
        "instructor_assignments",
        sa.Column("instructor_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("term_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["instructor_id"], ["instructors.id"]),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"]),
        sa.ForeignKeyConstraint(["term_id"], ["academic_terms.id"]),
        sa.UniqueConstraint("instructor_id", "course_id", "term_id", name="uq_instructor_course_term"),
    )
    op.create_index(op.f("ix_instructor_assignments_instructor_id"), "instructor_assignments", ["instructor_id"], unique=False)
    op.create_index(op.f("ix_instructor_assignments_course_id"), "instructor_assignments", ["course_id"], unique=False)
    op.create_index(op.f("ix_instructor_assignments_term_id"), "instructor_assignments", ["term_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()

    # Drop tables in reverse FK order
    op.drop_index(op.f("ix_instructor_assignments_term_id"), table_name="instructor_assignments")
    op.drop_index(op.f("ix_instructor_assignments_course_id"), table_name="instructor_assignments")
    op.drop_index(op.f("ix_instructor_assignments_instructor_id"), table_name="instructor_assignments")
    op.drop_table("instructor_assignments")

    op.drop_index(op.f("ix_course_management_officers_role"), table_name="course_management_officers")
    op.drop_index(op.f("ix_course_management_officers_staff_id"), table_name="course_management_officers")
    op.drop_index(op.f("ix_course_management_officers_user_id"), table_name="course_management_officers")
    op.drop_table("course_management_officers")

    op.drop_index(op.f("ix_students_enrollment_status"), table_name="students")
    op.drop_index(op.f("ix_students_student_id"), table_name="students")
    op.drop_index(op.f("ix_students_user_id"), table_name="students")
    op.drop_table("students")

    op.drop_index(op.f("ix_sections_instructor_id"), table_name="sections")
    op.drop_index(op.f("ix_sections_offering_id"), table_name="sections")
    op.drop_table("sections")

    op.drop_index(op.f("ix_instructors_department"), table_name="instructors")
    op.drop_index(op.f("ix_instructors_instructor_id"), table_name="instructors")
    op.drop_index(op.f("ix_instructors_user_id"), table_name="instructors")
    op.drop_table("instructors")

    op.drop_index(op.f("ix_course_offerings_term_id"), table_name="course_offerings")
    op.drop_index(op.f("ix_course_offerings_course_id"), table_name="course_offerings")
    op.drop_table("course_offerings")

    op.drop_index(op.f("ix_course_prerequisites_prerequisite_course_id"), table_name="course_prerequisites")
    op.drop_index(op.f("ix_course_prerequisites_course_id"), table_name="course_prerequisites")
    op.drop_table("course_prerequisites")

    op.drop_index(op.f("ix_courses_department"), table_name="courses")
    op.drop_index(op.f("ix_courses_code"), table_name="courses")
    op.drop_table("courses")

    op.drop_index(op.f("ix_academic_terms_term_name"), table_name="academic_terms")
    op.drop_table("academic_terms")

    # Drop the ENUM types created by Phase-0 columns. The other enums
    # (registrationstatus, gradeletter, etc.) are created by their
    # respective track migrations and dropped on those downgrades.
    for enum_name in ("officerrole", "enrollmentstatus"):
        sa.Enum(name=enum_name).drop(bind, checkfirst=True)
