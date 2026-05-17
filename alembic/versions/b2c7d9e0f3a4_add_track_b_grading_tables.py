"""add track b grading tables (breakdown, components, batch, scores)

Track B PR 2 schema. Reuses the existing ``gradesubmissionstatus``
enum created by the earlier grades-table migration; only the four new
tables are introduced here:

    assessment_breakdowns        — per (section, course) component plan
    assessment_components        — weighted rows of a breakdown
    grade_batches                — per (section, course) workflow object
    student_component_scores     — raw per-(batch, student, component) scores

No data migrations — these are additive workflow tables that start
empty. Track A's ``grades`` table is unchanged; Track B writes to it
at submit time via service code, not via a schema change here.

Revision ID: b2c7d9e0f3a4
Revises: a1c5d8e2f700
Create Date: 2026-05-16 19:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b2c7d9e0f3a4"
down_revision: Union[str, None] = "a1c5d8e2f700"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The ``gradesubmissionstatus`` enum already exists from
    # e1a2b3c4d508_add_grades_table.py; we reuse it here with
    # create_type=False so no DDL is attempted.
    grade_submission_status = postgresql.ENUM(
        "DRAFT", "SUBMITTED", "FLAGGED", "AUTHORISED", "REJECTED",
        name="gradesubmissionstatus", create_type=False,
    )

    # ── assessment_breakdowns ──
    op.create_table(
        "assessment_breakdowns",
        sa.Column("section_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("instructor_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("term_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "locked_at", postgresql.TIMESTAMP(timezone=True), nullable=True,
        ),
        sa.Column("created_by_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "is_deleted", sa.Boolean(),
            nullable=False, server_default=sa.text("false"),
        ),
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.ForeignKeyConstraint(["section_id"], ["sections.id"]),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"]),
        sa.ForeignKeyConstraint(["instructor_id"], ["instructors.id"]),
        sa.ForeignKeyConstraint(["term_id"], ["academic_terms.id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.UniqueConstraint(
            "section_id", "course_id",
            name="uq_breakdown_per_section_course",
        ),
    )
    op.create_index(
        op.f("ix_assessment_breakdowns_section_id"),
        "assessment_breakdowns", ["section_id"], unique=False,
    )
    op.create_index(
        op.f("ix_assessment_breakdowns_course_id"),
        "assessment_breakdowns", ["course_id"], unique=False,
    )
    op.create_index(
        op.f("ix_assessment_breakdowns_instructor_id"),
        "assessment_breakdowns", ["instructor_id"], unique=False,
    )
    op.create_index(
        op.f("ix_assessment_breakdowns_term_id"),
        "assessment_breakdowns", ["term_id"], unique=False,
    )

    # ── assessment_components ──
    op.create_table(
        "assessment_components",
        sa.Column("breakdown_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column(
            "max_score", sa.Float(), nullable=False, server_default="100.0",
        ),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["breakdown_id"], ["assessment_breakdowns.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "breakdown_id", "name",
            name="uq_component_name_per_breakdown",
        ),
        sa.CheckConstraint(
            "weight > 0 AND weight <= 100",
            name="ck_component_weight_range",
        ),
        sa.CheckConstraint(
            "max_score > 0",
            name="ck_component_max_score_positive",
        ),
    )
    op.create_index(
        op.f("ix_assessment_components_breakdown_id"),
        "assessment_components", ["breakdown_id"], unique=False,
    )

    # ── grade_batches ──
    op.create_table(
        "grade_batches",
        sa.Column("section_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("instructor_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("term_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("breakdown_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status", grade_submission_status,
            nullable=False, server_default="DRAFT",
        ),
        sa.Column(
            "submitted_at", postgresql.TIMESTAMP(timezone=True), nullable=True,
        ),
        sa.Column("submitted_by_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("instructor_justification", sa.Text(), nullable=True),
        sa.Column(
            "iteration_count", sa.Integer(),
            nullable=False, server_default="1",
        ),
        sa.Column(
            "is_deleted", sa.Boolean(),
            nullable=False, server_default=sa.text("false"),
        ),
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.ForeignKeyConstraint(["section_id"], ["sections.id"]),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"]),
        sa.ForeignKeyConstraint(["instructor_id"], ["instructors.id"]),
        sa.ForeignKeyConstraint(["term_id"], ["academic_terms.id"]),
        sa.ForeignKeyConstraint(
            ["breakdown_id"], ["assessment_breakdowns.id"],
        ),
        sa.ForeignKeyConstraint(["submitted_by_id"], ["users.id"]),
        sa.UniqueConstraint(
            "section_id", "course_id",
            name="uq_grade_batch_per_section_course",
        ),
        sa.CheckConstraint(
            "iteration_count >= 1",
            name="ck_grade_batch_iteration_positive",
        ),
    )
    op.create_index(
        op.f("ix_grade_batches_section_id"),
        "grade_batches", ["section_id"], unique=False,
    )
    op.create_index(
        op.f("ix_grade_batches_course_id"),
        "grade_batches", ["course_id"], unique=False,
    )
    op.create_index(
        op.f("ix_grade_batches_instructor_id"),
        "grade_batches", ["instructor_id"], unique=False,
    )
    op.create_index(
        op.f("ix_grade_batches_term_id"),
        "grade_batches", ["term_id"], unique=False,
    )
    op.create_index(
        op.f("ix_grade_batches_breakdown_id"),
        "grade_batches", ["breakdown_id"], unique=False,
    )
    op.create_index(
        op.f("ix_grade_batches_status"),
        "grade_batches", ["status"], unique=False,
    )

    # ── student_component_scores ──
    op.create_table(
        "student_component_scores",
        sa.Column("batch_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("student_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("component_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["grade_batches.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"]),
        sa.ForeignKeyConstraint(
            ["component_id"], ["assessment_components.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "batch_id", "student_id", "component_id",
            name="uq_score_per_batch_student_component",
        ),
        sa.CheckConstraint(
            "score IS NULL OR score >= 0",
            name="ck_score_non_negative",
        ),
    )
    op.create_index(
        op.f("ix_student_component_scores_batch_id"),
        "student_component_scores", ["batch_id"], unique=False,
    )
    op.create_index(
        op.f("ix_student_component_scores_student_id"),
        "student_component_scores", ["student_id"], unique=False,
    )
    op.create_index(
        op.f("ix_student_component_scores_component_id"),
        "student_component_scores", ["component_id"], unique=False,
    )


def downgrade() -> None:
    for index_name in (
        "ix_student_component_scores_component_id",
        "ix_student_component_scores_student_id",
        "ix_student_component_scores_batch_id",
    ):
        op.drop_index(op.f(index_name), table_name="student_component_scores")
    op.drop_table("student_component_scores")

    for index_name in (
        "ix_grade_batches_status",
        "ix_grade_batches_breakdown_id",
        "ix_grade_batches_term_id",
        "ix_grade_batches_instructor_id",
        "ix_grade_batches_course_id",
        "ix_grade_batches_section_id",
    ):
        op.drop_index(op.f(index_name), table_name="grade_batches")
    op.drop_table("grade_batches")

    op.drop_index(
        op.f("ix_assessment_components_breakdown_id"),
        table_name="assessment_components",
    )
    op.drop_table("assessment_components")

    for index_name in (
        "ix_assessment_breakdowns_term_id",
        "ix_assessment_breakdowns_instructor_id",
        "ix_assessment_breakdowns_course_id",
        "ix_assessment_breakdowns_section_id",
    ):
        op.drop_index(op.f(index_name), table_name="assessment_breakdowns")
    op.drop_table("assessment_breakdowns")
    # The ``gradesubmissionstatus`` enum is left in place — it's still
    # referenced by the ``grades`` table from e1a2b3c4d508.
