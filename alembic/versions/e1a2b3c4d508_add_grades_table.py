"""add grades table

Track-A precursor of the Track-B grading lifecycle. The Academic
Advisory Agent's demand-driven consult flow needs to resolve a
student's CGPA + completed-course set server-side rather than
trusting caller-provided values, which means a grade ledger has to
exist before the LLM consult endpoints can ship without
caller-provided overrides.

Shape is Track-B-aligned (lifecycle status, instructor + officer
audit fields, snapshot credit_hours) so the Track B grade-entry PR
can extend rather than replace this table.

Creates the ``gradeletter`` and ``gradesubmissionstatus`` enum types
which Track B will reuse.

Revision ID: e1a2b3c4d508
Revises: d6f1e9a2c700
Create Date: 2026-05-15 11:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "e1a2b3c4d508"
down_revision: Union[str, None] = "d6f1e9a2c700"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_GRADE_LETTER = (
    "A", "A-", "B+", "B", "B-", "C+", "C", "C-",
    "D", "F", "I", "NG",
)
_GRADE_SUBMISSION_STATUS = (
    "DRAFT", "SUBMITTED", "FLAGGED", "AUTHORISED", "REJECTED",
)


def upgrade() -> None:
    bind = op.get_bind()

    grade_letter = postgresql.ENUM(
        *_GRADE_LETTER, name="gradeletter", create_type=False,
    )
    grade_submission_status = postgresql.ENUM(
        *_GRADE_SUBMISSION_STATUS,
        name="gradesubmissionstatus", create_type=False,
    )
    grade_letter.create(bind, checkfirst=True)
    grade_submission_status.create(bind, checkfirst=True)

    op.create_table(
        "grades",
        sa.Column("student_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("term_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("section_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("letter_grade", grade_letter, nullable=True),
        sa.Column("numeric_score", sa.Float(), nullable=True),
        sa.Column("credit_hours", sa.Integer(), nullable=False),
        sa.Column("grade_points", sa.Float(), nullable=True),
        sa.Column(
            "status", grade_submission_status,
            nullable=False, server_default="DRAFT",
        ),
        sa.Column("entered_by_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "entered_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column("authorised_by_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "authorised_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"]),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"]),
        sa.ForeignKeyConstraint(["term_id"], ["academic_terms.id"]),
        sa.ForeignKeyConstraint(["section_id"], ["sections.id"]),
        sa.ForeignKeyConstraint(["entered_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["authorised_by_id"], ["users.id"]),
        sa.UniqueConstraint(
            "student_id", "course_id", "term_id",
            name="uq_grade_per_student_course_term",
        ),
        sa.CheckConstraint(
            "numeric_score IS NULL OR "
            "(numeric_score >= 0 AND numeric_score <= 100)",
            name="ck_grade_score_range",
        ),
        sa.CheckConstraint(
            "credit_hours BETWEEN 1 AND 12",
            name="ck_grade_credit_hours_range",
        ),
    )
    op.create_index(op.f("ix_grades_student_id"), "grades", ["student_id"], unique=False)
    op.create_index(op.f("ix_grades_course_id"), "grades", ["course_id"], unique=False)
    op.create_index(op.f("ix_grades_term_id"), "grades", ["term_id"], unique=False)
    op.create_index(op.f("ix_grades_section_id"), "grades", ["section_id"], unique=False)
    op.create_index(op.f("ix_grades_status"), "grades", ["status"], unique=False)


def downgrade() -> None:
    for index_name in (
        "ix_grades_status",
        "ix_grades_section_id",
        "ix_grades_term_id",
        "ix_grades_course_id",
        "ix_grades_student_id",
    ):
        op.drop_index(op.f(index_name), table_name="grades")
    op.drop_table("grades")

    bind = op.get_bind()
    for enum_name in ("gradesubmissionstatus", "gradeletter"):
        postgresql.ENUM(name=enum_name).drop(bind, checkfirst=True)
