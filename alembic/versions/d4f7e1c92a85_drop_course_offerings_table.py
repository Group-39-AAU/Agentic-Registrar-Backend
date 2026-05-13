"""drop course_offerings table

Revision ID: d4f7e1c92a85
Revises: c8b6e3a25410
Create Date: 2026-05-07 16:00:00.000000

The CourseOffering table was a per-(course, term) marker that lost
its only structural job in the cohort migration: Sections used to
FK back to it but now key off (term, department, semester) directly.
Its capacity / section_count columns were never read by application
code, no endpoint queried it, no test asserted on it.

Drop it. The seed script no longer creates rows; the model class is
gone. Restoring it later would mean reverting this migration AND
the model deletion.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "d4f7e1c92a85"
down_revision: Union[str, None] = "c8b6e3a25410"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Wipe rows first so the DROP doesn't conflict with stale FK
    # graph state. (The cohort migration already severed every FK
    # into this table, so this is belt-and-braces.)
    op.execute("DELETE FROM course_offerings")
    op.drop_table("course_offerings")


def downgrade() -> None:
    op.create_table(
        "course_offerings",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "course_id", postgresql.UUID(as_uuid=True), nullable=False,
        ),
        sa.Column(
            "term_id", postgresql.UUID(as_uuid=True), nullable=False,
        ),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("section_count", sa.Integer(), nullable=False),
        sa.Column(
            "is_deleted", sa.Boolean(), nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"]),
        sa.ForeignKeyConstraint(["term_id"], ["academic_terms.id"]),
        sa.UniqueConstraint(
            "course_id", "term_id", name="uq_course_offering_per_term",
        ),
        sa.CheckConstraint(
            "capacity > 0", name="ck_offering_capacity_positive",
        ),
        sa.CheckConstraint(
            "section_count > 0", name="ck_offering_section_count_positive",
        ),
    )
    op.create_index(
        "ix_course_offerings_course_id", "course_offerings", ["course_id"],
    )
    op.create_index(
        "ix_course_offerings_term_id", "course_offerings", ["term_id"],
    )
