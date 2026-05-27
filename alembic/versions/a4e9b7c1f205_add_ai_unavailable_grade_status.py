"""add_ai_unavailable_grade_status

Revision ID: a4e9b7c1f205
Revises: e6c1d4f9a7b3
Create Date: 2026-05-27 00:00:00.000000

Adds ``AI_UNAVAILABLE`` to the ``gradesubmissionstatus`` enum so a
grade batch whose Track B agent run could not produce a verdict
(LLM unreachable, malformed response, missing client) lands in a
distinct state instead of sitting at SUBMITTED. The state unlocks
the instructor-rerun path; SUBMITTED is reserved for batches the
agent actually approved.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "a4e9b7c1f205"
down_revision: Union[str, None] = "e6c1d4f9a7b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE gradesubmissionstatus "
        "ADD VALUE IF NOT EXISTS 'AI_UNAVAILABLE'"
    )


def downgrade() -> None:
    # PostgreSQL enum values are not safely removable in-place.
    pass
