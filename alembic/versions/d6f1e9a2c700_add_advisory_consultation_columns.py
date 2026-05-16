"""add advisory consultation columns

Adds two nullable columns to ``advisory_recommendations`` so the table
can hold both legacy submit-time rule-engine evaluations (mode is NULL)
and the new demand-driven LLM-backed consultations:

  - ``consultation_mode`` : enum ConsultationMode discriminating which
    of the three /advisory/consult/* endpoints produced the row.
  - ``graduation_impact`` : JSONB blob carrying the LLM's graduation
    trajectory analysis. Shape is informational and not validated at
    the column level — see AdvisoryRecommendation docstring.

Both columns are nullable because every existing row was produced by
the rule engine before the consult feature shipped, and the rule path
keeps writing rows with these columns null.

Revision ID: d6f1e9a2c700
Revises: 9d8c7b6a5f4e
Create Date: 2026-05-15 09:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d6f1e9a2c700"
down_revision: Union[str, None] = "9d8c7b6a5f4e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CONSULTATION_MODE = ("PRE_REGISTRATION", "REGISTRATION_PLAN", "ADD_DROP")


def upgrade() -> None:
    bind = op.get_bind()

    consultation_mode = postgresql.ENUM(
        *_CONSULTATION_MODE, name="consultationmode", create_type=False,
    )
    consultation_mode.create(bind, checkfirst=True)

    op.add_column(
        "advisory_recommendations",
        sa.Column("consultation_mode", consultation_mode, nullable=True),
    )
    op.add_column(
        "advisory_recommendations",
        sa.Column(
            "graduation_impact",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.create_index(
        op.f("ix_advisory_recommendations_consultation_mode"),
        "advisory_recommendations",
        ["consultation_mode"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_advisory_recommendations_consultation_mode"),
        table_name="advisory_recommendations",
    )
    op.drop_column("advisory_recommendations", "graduation_impact")
    op.drop_column("advisory_recommendations", "consultation_mode")

    bind = op.get_bind()
    consultation_mode = postgresql.ENUM(
        *_CONSULTATION_MODE, name="consultationmode",
    )
    consultation_mode.drop(bind, checkfirst=True)
