"""add department to students

Revision ID: e4f5a8c12b3d
Revises: d2e8f6a1c4b9
Create Date: 2026-05-04 18:30:00.000000

Backs the program-aligned curriculum filter: a student in
Software Engineering should only see SE courses in
``/courses/me/curriculum``, never EE / ME / etc. The column is
denormalised from Enrollment.department at onboarding time so the
curriculum lookup doesn't need a cross-module join.

Nullable so legacy rows seeded before this migration don't blow up;
new rows are always populated by OnboardingService.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e4f5a8c12b3d"
down_revision: Union[str, None] = "d2e8f6a1c4b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "students",
        sa.Column("department", sa.String(length=100), nullable=True),
    )
    op.create_index(
        "ix_students_department", "students", ["department"], unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_students_department", table_name="students")
    op.drop_column("students", "department")
