"""
Ensure every Ethiopian-phase AcademicTerm row exists in the DB.

This script is an idempotent backfill: it inserts any (term_name,
phase) row from ``NEW_TERMS`` that is missing, and leaves existing
rows untouched. It is the seed-side counterpart to the Alembic chain:

  * Migration ``a1b2c3d4e5f6`` adds the ``phase`` column.
  * Migration ``b2c3d4e5f6a7`` strips the " Phase One/Two" suffix
    and swaps the uniqueness model to UNIQUE(term_name, phase).
  * This script then guarantees the four canonical rows exist —
    useful on dev DBs that predate the seed file's term update.

Re-running is a no-op once all four rows are in place.

Run:
    ./venv/bin/python scripts/migrate_terms_to_ethiopian_phases.py
"""
from __future__ import annotations

import asyncio
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import AsyncSessionLocal
from app.modules.course.models import AcademicTerm
from app.shared.enums import AcademicPhase


# Source-of-truth: must match the four entries declared in
# ``scripts/seed_course.py`` (TERMS). If the seed file changes, mirror
# the change here.
NEW_TERMS = [
    {
        "term_name": "2025/2026",
        "phase": AcademicPhase.ONE,
        "start_date": date(2025, 9, 1),
        "end_date":   date(2026, 1, 31),
        "is_open": False,
        "description": "Phase One of the 2025/2026 academic year (Sep–Jan).",
    },
    {
        "term_name": "2025/2026",
        "phase": AcademicPhase.TWO,
        "start_date": date(2026, 2, 1),
        "end_date":   date(2026, 6, 30),
        "is_open": True,
        "description": "Phase Two of the 2025/2026 academic year (Feb–Jun).",
    },
    {
        "term_name": "2026/2027",
        "phase": AcademicPhase.ONE,
        "start_date": date(2026, 9, 1),
        "end_date":   date(2027, 1, 31),
        "is_open": False,
        "description": "Phase One of the 2026/2027 academic year (Sep–Jan).",
    },
    {
        "term_name": "2026/2027",
        "phase": AcademicPhase.TWO,
        "start_date": date(2027, 2, 1),
        "end_date":   date(2027, 6, 30),
        "is_open": False,
        "description": "Phase Two of the 2026/2027 academic year (Feb–Jun).",
    },
]

async def _insert_missing(db: AsyncSession) -> None:
    """
    Insert any (term_name, phase) row from NEW_TERMS that isn't already
    present. Existing rows are left untouched.
    """
    for spec in NEW_TERMS:
        existing = (
            await db.execute(
                select(AcademicTerm).where(
                    AcademicTerm.term_name == spec["term_name"],
                    AcademicTerm.phase == spec["phase"],
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            print(
                f"  ↻  '{spec['term_name']}' (phase {spec['phase'].value}) "
                "already exists — skipping insert."
            )
            continue
        row = AcademicTerm(**spec)
        db.add(row)
        await db.flush()
        print(
            f"  ✅ Inserted '{row.term_name}' (phase {row.phase.value})  "
            f"(id={row.id}, is_open={spec['is_open']})"
        )


async def main() -> None:
    print("Ensuring Ethiopian-phase academic terms exist ...")
    async with AsyncSessionLocal() as db:  # type: AsyncSession
        await _insert_missing(db)
        await db.commit()

    print()
    print("─" * 58)
    print("Done. Final state:")
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(AcademicTerm)
                .where(AcademicTerm.is_deleted == False)  # noqa: E712
                .order_by(AcademicTerm.start_date.asc())
            )
        ).scalars().all()
        for r in rows:
            flag = "OPEN " if r.is_open else "     "
            print(
                f"  [{flag}] {r.term_name}  phase={r.phase.value:<3}  "
                f"{r.start_date.isoformat()} → {r.end_date.isoformat()}"
            )


if __name__ == "__main__":
    asyncio.run(main())
