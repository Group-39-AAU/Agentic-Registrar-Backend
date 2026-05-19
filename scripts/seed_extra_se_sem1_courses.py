"""
Add extra Software Engineering, semester-1 catalog courses for testing.

The initial-curriculum picker (POST /api/v1/courses/me/available-courses)
filters by ``course.department == student.department`` AND
``course.semester == student.current_semester``. Student UGR/9999/14
is in Software Engineering, semester 1, so only SE-sem-1 catalog rows
appear. The base seed (scripts/seed_course.py) only ships 4 such
courses (SE101-SE104). This script adds 6 more so the "add course"
flow has more options to exercise.

Idempotent: re-runs skip existing codes.

Usage:
    docker exec agentic-registrar-backend-app-1 python scripts/seed_extra_se_sem1_courses.py
"""

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.modules.auth.models import User  # noqa: F401  ensure mapper config
from app.modules.course.models import Course


# Same namespace as scripts/seed_course.py so deterministic UUIDs
# stay consistent across both seeders.
_NS = uuid.UUID("c0a3e000-0000-4000-8000-000000000000")


def _uid(*parts: str) -> uuid.UUID:
    return uuid.uuid5(_NS, "/".join(parts))


# (code, title, credit_hours)
# All rows are department="Software Engineering", semester=1.
# Codes follow the SE{sem}{slot:02d} pattern from seed_course.py,
# starting at slot 05 so they never collide with the base seed
# (which uses slots 01-04).
EXTRA_SE_SEM1 = [
    ("SE105", "Introduction to Computing",       3),
    ("SE106", "Communicative English Skills",    3),
    ("SE107", "Civics & Ethical Education",      2),
    ("SE108", "Logic & Critical Thinking",       2),
    ("SE109", "Inclusiveness",                   1),
    ("SE110", "General Psychology",              3),
]

DEPARTMENT = "Software Engineering"
SEMESTER = 1


async def main() -> None:
    engine = create_async_engine(str(settings.DATABASE_URL))
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        existing = {
            c.code
            for c in (await session.execute(select(Course))).scalars().all()
        }

        added: list[str] = []
        for code, title, credits in EXTRA_SE_SEM1:
            if code in existing:
                continue
            session.add(Course(
                id=_uid("course", code),
                code=code,
                title=title,
                credit_hours=credits,
                semester=SEMESTER,
                department=DEPARTMENT,
            ))
            added.append(code)

        await session.commit()

        print(f"Added {len(added)} extra SE sem-1 courses: {added}")
        print(f"Skipped (already present): "
              f"{[c for c, *_ in EXTRA_SE_SEM1 if c in existing]}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
