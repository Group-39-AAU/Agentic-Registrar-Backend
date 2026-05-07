"""add INSTRUCTOR to UserRole enum

Revision ID: c8b6e3a25410
Revises: a7c4e2f8b910
Create Date: 2026-05-07 14:00:00.000000

Instructors used to be seeded with role=AGENT (which is meant for
LangGraph AI agents). The new flow seeds them with role=INSTRUCTOR
so the auth layer can route them correctly to the portal — same
PIN + must_change_password lifecycle as students.

Postgres enum values are append-only; this migration is a one-line
ALTER TYPE. No downgrade is provided because Postgres has no
in-place enum-value removal.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "c8b6e3a25410"
down_revision: Union[str, None] = "a7c4e2f8b910"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'INSTRUCTOR'")


def downgrade() -> None:
    # Postgres enum values are not safely removable in-place.
    pass
