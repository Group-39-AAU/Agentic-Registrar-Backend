"""Add FLAGGED_FOR_REVIEW to ApplicationStatus enum

Revision ID: a86f078e564c
Revises: e3b0f4801228
Create Date: 2026-03-16 11:15:24.912835
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a86f078e564c'
down_revision: Union[str, None] = 'e3b0f4801228'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add FLAGGED_FOR_REVIEW to the existing applicationstatus ENUM
    # Note: Postgres ALTER TYPE ... ADD VALUE cannot be executed inside a transaction block in older versions,
    # but Alembic usually handles it if we commit.
    op.execute("ALTER TYPE applicationstatus ADD VALUE IF NOT EXISTS 'FLAGGED_FOR_REVIEW';")


def downgrade() -> None:
    # Postgres doesn't easily support dropping an enum value.
    # We would have to recreate the type. For safety we do nothing here.
    pass
