"""add_changes_requested_status

Revision ID: 9f2d4c8a1b7e
Revises: 7c1a2b3d4e5f
Create Date: 2026-04-27 18:00:00.000000
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "9f2d4c8a1b7e"
down_revision: Union[str, None] = "7c1a2b3d4e5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE applicationstatus ADD VALUE IF NOT EXISTS 'CHANGES_REQUESTED'"
    )


def downgrade() -> None:
    # PostgreSQL enum values are not safely removable in-place.
    pass
