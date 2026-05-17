"""add grade_authorisation_decisions

Department-head decision audit trail for Track B (PR 4). One
immutable row per terminal decision a DH makes on a grade batch.
Append-only — overturning a decision means writing a new row,
never updating an existing one.

The ``decision`` value records BOTH the outcome (authorised /
rejected) and whether the DH agreed with or overrode the agent's
prior verdict, so the audit log preserves the full chain of
who decided what against which agent reading:

  AUTHORISED              — agent APPROVED + DH agrees → grades go official
  REJECTED                — agent FLAGGED + DH agrees → back to instructor
  OVERRODE_AGENT_APPROVAL — agent APPROVED + DH rejects → back to instructor
  OVERRODE_AGENT_FLAG     — agent FLAGGED + DH authorises → grades go official

Justification text is required for everything except ``AUTHORISED``
(accepting a clean agent-APPROVE is the only path where no written
reason is required of the DH).

Revision ID: d4e9f3b8c5a2
Revises: c3d8e1f2a5b6
Create Date: 2026-05-17 11:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d4e9f3b8c5a2"
down_revision: Union[str, None] = "c3d8e1f2a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_DECISION_VALUES = (
    "AUTHORISED", "REJECTED",
    "OVERRODE_AGENT_APPROVAL", "OVERRODE_AGENT_FLAG",
)


def upgrade() -> None:
    op.create_table(
        "grade_authorisation_decisions",
        sa.Column("batch_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(40), nullable=False),
        sa.Column("department_head_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "decision_at", postgresql.TIMESTAMP(timezone=True),
            nullable=False, server_default=sa.text("now()"),
        ),
        sa.Column("justification", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["grade_batches.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["department_head_id"], ["users.id"]),
        sa.CheckConstraint(
            "decision IN ('"
            + "', '".join(_DECISION_VALUES)
            + "')",
            name="ck_authorisation_decision_value",
        ),
        sa.CheckConstraint(
            "iteration >= 1",
            name="ck_authorisation_iteration_positive",
        ),
    )
    op.create_index(
        op.f("ix_grade_authorisation_decisions_batch_id"),
        "grade_authorisation_decisions", ["batch_id"], unique=False,
    )
    op.create_index(
        op.f("ix_grade_authorisation_decisions_department_head_id"),
        "grade_authorisation_decisions",
        ["department_head_id"], unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_grade_authorisation_decisions_department_head_id"),
        table_name="grade_authorisation_decisions",
    )
    op.drop_index(
        op.f("ix_grade_authorisation_decisions_batch_id"),
        table_name="grade_authorisation_decisions",
    )
    op.drop_table("grade_authorisation_decisions")
