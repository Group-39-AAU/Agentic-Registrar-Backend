"""shorten academic_terms.term_name to year-only

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-16 12:15:00.000000

The phase is now a first-class column, so the ``term_name`` no longer
needs to encode it. Renames the term_name from ``"<year> Phase <N>"``
to just ``"<year>"`` (e.g. ``"2025/2026 Phase One"`` → ``"2025/2026"``)
and swaps the uniqueness model:

  before: UNIQUE(term_name)
  after:  UNIQUE(term_name, phase)

The composite key still prevents duplicate (year, phase) pairs but
lets both phases of the same year share the same label.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop the column-level unique index (set up by SQLAlchemy when
    # term_name had ``unique=True, index=True``). The replacement
    # non-unique index keeps lookups by term_name fast.
    op.drop_index("ix_academic_terms_term_name", table_name="academic_terms")

    # Strip the " Phase One" / " Phase Two" suffix in place. Anything
    # that does not match the pattern is left untouched so operator-
    # entered term names survive untouched.
    op.execute(
        """
        UPDATE academic_terms
           SET term_name = regexp_replace(term_name, '\\s+Phase\\s+(One|Two)$', '', 'i')
         WHERE term_name ~* '\\s+Phase\\s+(One|Two)$';
        """
    )

    op.create_index(
        "ix_academic_terms_term_name",
        "academic_terms",
        ["term_name"],
        unique=False,
    )
    op.create_unique_constraint(
        "uq_academic_terms_year_phase",
        "academic_terms",
        ["term_name", "phase"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_academic_terms_year_phase",
        "academic_terms",
        type_="unique",
    )
    op.drop_index("ix_academic_terms_term_name", table_name="academic_terms")

    # Reinstate the old labels so the unique-on-term_name index can
    # be created again without collision. Phase ONE → "<year> Phase One",
    # phase TWO → "<year> Phase Two".
    op.execute(
        """
        UPDATE academic_terms
           SET term_name = term_name || ' Phase ' ||
               CASE WHEN phase = 'ONE' THEN 'One' ELSE 'Two' END
         WHERE term_name !~* '\\s+Phase\\s+(One|Two)$';
        """
    )

    op.create_index(
        "ix_academic_terms_term_name",
        "academic_terms",
        ["term_name"],
        unique=True,
    )
