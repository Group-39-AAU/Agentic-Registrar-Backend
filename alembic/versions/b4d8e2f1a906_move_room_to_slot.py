"""move room from sections to class_schedule_slots

Revision ID: b4d8e2f1a906
Revises: a3c91d4f2e08
Create Date: 2026-05-13 17:15:00.000000

The cohort model now lets the same Section attend different courses
in different classrooms — Calculus in SE-101 then Programming Lab in
SE-LAB-1, for example. So the authoritative "where does this class
meet" pointer moves from ``Section.room`` (one room per section) to
``ClassScheduleSlot.room`` (one room per weekly meeting).

Upgrade:
  - Add ``class_schedule_slots.room: String(50) NULL``.
  - Backfill: each slot inherits its section's old room.
  - Drop ``sections.room``.

Downgrade is approximate: ``sections.room`` is re-added and filled
from any of the section's slot rooms (pick one arbitrarily). If a
section had slots in multiple rooms under the new model, the
downgrade collapses them to one.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b4d8e2f1a906"
down_revision: Union[str, None] = "a3c91d4f2e08"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "class_schedule_slots",
        sa.Column("room", sa.String(length=50), nullable=True),
    )
    op.execute(
        """
        UPDATE class_schedule_slots cs
        SET room = s.room
        FROM sections s
        WHERE cs.section_id = s.id AND s.room IS NOT NULL
        """
    )
    op.drop_column("sections", "room")


def downgrade() -> None:
    op.add_column(
        "sections",
        sa.Column("room", sa.String(length=50), nullable=True),
    )
    # Pick one room per section from any of its slots — approximate
    # since the new model permits multi-room sections.
    op.execute(
        """
        UPDATE sections s
        SET room = sub.room
        FROM (
            SELECT DISTINCT ON (section_id) section_id, room
            FROM class_schedule_slots
            WHERE room IS NOT NULL
        ) sub
        WHERE s.id = sub.section_id
        """
    )
    op.drop_column("class_schedule_slots", "room")
