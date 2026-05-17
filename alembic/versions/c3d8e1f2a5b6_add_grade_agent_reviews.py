"""add grade_agent_reviews (append-only Track B agent audit trail)

One row per GradingMonitorAgent run. The instructor sees the latest;
the department head (PR 4) sees the full history of iterations the
instructor went through. Append-only — re-running the agent on the
same batch writes a NEW row, never updates an existing one.

Fields:
  - batch_id          FK to grade_batches (cascade delete with batch)
  - iteration         the grade_batches.iteration_count this run is for
  - verdict           "APPROVE" | "FLAG" | "PENDING"
                       PENDING means the LLM call failed and the
                       department head should manually re-trigger.
                       Held as a String column (not an enum) because
                       it's an agent-internal value, not a workflow
                       status — Postgres-enum migration is more
                       maintenance cost than the safety is worth.
  - tool_findings     JSON dict of every deterministic tool's output
                       (class stats, distribution, outliers, etc.).
                       Always populated, even when the LLM fails.
  - llm_reasoning     plain-English explanation written by the LLM.
                       NULL only when verdict == "PENDING".
  - flags             JSON list of structured concerns the agent or
                       LLM identified. Always present; empty list
                       on APPROVE.
  - agent_id          which agent instance ran (matches BaseAgent's
                       ``agent_id`` field for cross-referencing the
                       SystemAuditLog stream).

Revision ID: c3d8e1f2a5b6
Revises: b2c7d9e0f3a4
Create Date: 2026-05-17 10:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c3d8e1f2a5b6"
down_revision: Union[str, None] = "b2c7d9e0f3a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "grade_agent_reviews",
        sa.Column("batch_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=False),
        sa.Column("verdict", sa.String(20), nullable=False),
        sa.Column(
            "tool_findings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("llm_reasoning", sa.Text(), nullable=True),
        sa.Column(
            "flags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("agent_id", sa.String(120), nullable=False),
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
        sa.CheckConstraint(
            "verdict IN ('APPROVE', 'FLAG', 'PENDING')",
            name="ck_grade_agent_review_verdict",
        ),
        sa.CheckConstraint(
            "iteration >= 1",
            name="ck_grade_agent_review_iteration_positive",
        ),
    )
    op.create_index(
        op.f("ix_grade_agent_reviews_batch_id"),
        "grade_agent_reviews", ["batch_id"], unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_grade_agent_reviews_batch_id"),
        table_name="grade_agent_reviews",
    )
    op.drop_table("grade_agent_reviews")
