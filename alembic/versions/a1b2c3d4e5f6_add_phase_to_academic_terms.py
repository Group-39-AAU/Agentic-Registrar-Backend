"""add phase to academic_terms

Revision ID: a1b2c3d4e5f6
Revises: 9d8c7b6a5f4e
Create Date: 2026-05-16 12:00:00.000000

Adds the Ethiopian-context ``phase`` column on ``academic_terms``.
Each academic year is split into two phases:

  ONE — September → end of January
  TWO — February  → end of June

Migration shape:
  1. Create the ``academicphase`` enum type in Postgres.
  2. Add the column as nullable (so existing rows don't violate NOT NULL).
  3. Backfill from ``term_name``: rows containing "Phase One" → ONE,
     "Phase Two" → TWO. Other names default to ONE so the NOT NULL
     constraint can be applied; operators should reconcile manually
     after the migration.
  4. Set NOT NULL and add an index for phase-filtered reads.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "9d8c7b6a5f4e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_PHASE_ENUM_NAME = "academicphase"


def upgrade() -> None:
    phase_enum = sa.Enum("ONE", "TWO", name=_PHASE_ENUM_NAME)
    phase_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "academic_terms",
        sa.Column(
            "phase",
            sa.Enum("ONE", "TWO", name=_PHASE_ENUM_NAME, create_type=False),
            nullable=True,
            comment=(
                "Ethiopian-context phase within the academic year: "
                "ONE = Sep–Jan, TWO = Feb–Jun."
            ),
        ),
    )

    # Backfill from term_name. Anything that does NOT explicitly say
    # "Phase Two" falls back to ONE — operators can fix up edge cases
    # after the migration completes.
    op.execute(
        """
        UPDATE academic_terms
           SET phase = CASE
               WHEN term_name ILIKE '%phase two%' THEN 'TWO'::academicphase
               ELSE 'ONE'::academicphase
           END
         WHERE phase IS NULL;
        """
    )

    op.alter_column("academic_terms", "phase", nullable=False)
    op.create_index(
        "ix_academic_terms_phase", "academic_terms", ["phase"], unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_academic_terms_phase", table_name="academic_terms")
    op.drop_column("academic_terms", "phase")
    sa.Enum(name=_PHASE_ENUM_NAME).drop(op.get_bind(), checkfirst=True)
