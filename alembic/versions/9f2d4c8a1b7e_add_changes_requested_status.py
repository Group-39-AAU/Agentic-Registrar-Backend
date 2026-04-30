"""add_changes_requested_status

Revision ID: 9f2d4c8a1b7e
Revises: c1a3e000a001
Create Date: 2026-04-27 18:00:00.000000

Re-chained off the course-management head (c1a3e000a001) instead of
7c1a2b3d4e5f to collapse a two-head branch in the migration graph.
The CHANGES_REQUESTED enum value is admission-only and has no actual
dependency on the course-management tables, so the new ordering is
purely structural.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "9f2d4c8a1b7e"
down_revision: Union[str, None] = "c1a3e000a001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE applicationstatus ADD VALUE IF NOT EXISTS 'CHANGES_REQUESTED'"
    )


def downgrade() -> None:
    # PostgreSQL enum values are not safely removable in-place.
    pass
