"""add senate grade letters A+, W, DO, P

Track C PR C0 — Senate Article 90 alignment.

Extends the existing ``gradeletter`` Postgres ENUM with the four
letters required by AAU Senate Legislation Article 90 that were
missing from the original Track B definition:

  A+  — Article 90.1, [90, 100] range, "Excellent / First class with
        Great distinction". 4.00 grade points (same as A).
  W   — Article 90.7.2, "Withdrawn" administrative mark for students
        who formally withdraw within 8 weeks. Excluded from GPA
        (Art 90.7.4).
  DO  — Article 90.7.3, "Dropout" administrative mark for students
        who fail to follow withdrawal procedures. Excluded from GPA.
  P   — Article 90.7.6, "Pass" mark for non-credit work. Excluded
        from GPA.

This is a pure ENUM-extension migration — no table changes, no data
migration. Existing rows (which use the pre-Senate cutoffs in
letter_for_numeric) remain valid; subsequent grade submissions will
use the corrected Senate cutoffs.

Note: Postgres only allows ALTER TYPE ... ADD VALUE outside a
transaction in versions prior to 12. Since the project targets
modern Postgres, the ADD VALUE statements run inline.

Revision ID: c5e9a1b3d702
Revises: d4e9f3b8c5a2
Create Date: 2026-05-17 18:00:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = "c5e9a1b3d702"
down_revision: Union[str, None] = "d4e9f3b8c5a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Order matters for downgrade rebuild. The labels mirror the
# GradeLetter enum's declaration order so the type's value list
# stays human-readable in psql \dT+.
_SENATE_ORDER = (
    "A+", "A", "A-", "B+", "B", "B-",
    "C+", "C", "C-", "D", "F",
    "I", "NG", "W", "DO", "P",
)
_LEGACY_ORDER = (
    "A", "A-", "B+", "B", "B-", "C+", "C", "C-",
    "D", "F", "I", "NG",
)


def upgrade() -> None:
    # ADD VALUE is idempotent with IF NOT EXISTS, so repeat-runs and
    # partial migrations are safe.
    op.execute("ALTER TYPE gradeletter ADD VALUE IF NOT EXISTS 'A+' BEFORE 'A'")
    op.execute("ALTER TYPE gradeletter ADD VALUE IF NOT EXISTS 'W'")
    op.execute("ALTER TYPE gradeletter ADD VALUE IF NOT EXISTS 'DO'")
    op.execute("ALTER TYPE gradeletter ADD VALUE IF NOT EXISTS 'P'")


def downgrade() -> None:
    """
    Postgres has no native DROP VALUE for an ENUM type. The standard
    rollback recipe is to rename the existing type aside, recreate it
    with the original value set, ALTER every column to the new type
    casting through text, and drop the renamed type.
    """
    bind = op.get_bind()

    # Columns that use ``gradeletter`` — keep this list in sync with
    # any future migrations that bind a column to this type.
    affected_columns = (
        ("grades", "letter_grade"),
    )

    # 1. Rename the live type aside.
    op.execute("ALTER TYPE gradeletter RENAME TO gradeletter_old")

    # 2. Recreate the type with the legacy value list.
    legacy_values = ", ".join(f"'{v}'" for v in _LEGACY_ORDER)
    op.execute(f"CREATE TYPE gradeletter AS ENUM ({legacy_values})")

    # 3. Re-bind every column, casting through text and dropping any
    #    row whose value isn't representable in the legacy set.
    for table, column in affected_columns:
        # Defensive: nullify rows that hold a value we're about to drop.
        op.execute(
            f"UPDATE {table} SET {column} = NULL "
            f"WHERE {column}::text IN ('A+', 'W', 'DO', 'P')"
        )
        op.execute(
            f"ALTER TABLE {table} "
            f"ALTER COLUMN {column} TYPE gradeletter "
            f"USING ({column}::text::gradeletter)"
        )

    # 4. Drop the renamed-aside type.
    op.execute("DROP TYPE gradeletter_old")
    _ = bind  # unused but kept for symmetry with other migrations
