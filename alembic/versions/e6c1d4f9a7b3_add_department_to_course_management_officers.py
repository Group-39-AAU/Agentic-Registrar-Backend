"""add department to course_management_officers

Revision ID: e6c1d4f9a7b3
Revises: d8f2a3b4e605
Create Date: 2026-05-24 18:00:00.000000

Department-Head officers are now scoped to a single department —
they can only run section allocation + schedule generation for the
department they own. The new column backs that scoping check in
``SchedulingService._require_dh_or_admin``.

Nullable in the DB because plain REGISTRAR_OFFICER officers (and
legacy rows) don't carry a department. The application layer requires
a non-null value for officers whose ``role == DEPARTMENT_HEAD``.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e6c1d4f9a7b3"
down_revision: Union[str, None] = "d8f2a3b4e605"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "course_management_officers",
        sa.Column("department", sa.String(length=100), nullable=True),
    )
    op.create_index(
        "ix_course_management_officers_department",
        "course_management_officers",
        ["department"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_course_management_officers_department",
        table_name="course_management_officers",
    )
    op.drop_column("course_management_officers", "department")
