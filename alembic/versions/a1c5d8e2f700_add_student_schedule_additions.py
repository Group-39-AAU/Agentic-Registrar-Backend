"""add student_schedule_additions

Per-student schedule delta table backing the add/drop schedule
re-allocation flow:

  - When the officer applies an add/drop batch, dropped courses are
    auto-removed from the student's effective schedule (filtered out
    of the cohort slot list at read time) and added courses land
    without slots — the student then picks a section via
    AcademicSchedulingAgent.propose_options_for_course and the
    selected slots are written here.
  - Cohort scheduling is unchanged; this is a pure overlay.

Revision ID: a1c5d8e2f700
Revises: 58272130a499
Create Date: 2026-05-16 14:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a1c5d8e2f700"
down_revision: Union[str, None] = "58272130a499"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "student_schedule_additions",
        sa.Column("registration_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("schedule_slot_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("source_section_id", sa.UUID(as_uuid=True), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["registration_id"], ["registrations.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["schedule_slot_id"], ["class_schedule_slots.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"]),
        sa.ForeignKeyConstraint(["source_section_id"], ["sections.id"]),
        sa.UniqueConstraint(
            "registration_id", "schedule_slot_id",
            name="uq_student_addition_per_slot",
        ),
    )
    op.create_index(
        op.f("ix_student_schedule_additions_registration_id"),
        "student_schedule_additions", ["registration_id"], unique=False,
    )
    op.create_index(
        op.f("ix_student_schedule_additions_schedule_slot_id"),
        "student_schedule_additions", ["schedule_slot_id"], unique=False,
    )
    op.create_index(
        op.f("ix_student_schedule_additions_course_id"),
        "student_schedule_additions", ["course_id"], unique=False,
    )
    op.create_index(
        op.f("ix_student_schedule_additions_source_section_id"),
        "student_schedule_additions", ["source_section_id"], unique=False,
    )


def downgrade() -> None:
    for index_name in (
        "ix_student_schedule_additions_source_section_id",
        "ix_student_schedule_additions_course_id",
        "ix_student_schedule_additions_schedule_slot_id",
        "ix_student_schedule_additions_registration_id",
    ):
        op.drop_index(
            op.f(index_name), table_name="student_schedule_additions",
        )
    op.drop_table("student_schedule_additions")
