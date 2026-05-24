"""
Root pytest configuration and shared fixtures.

Provides:
    - pytest-asyncio configuration (auto mode)
    - ``async_engine`` — fresh in-memory SQLite engine per test session,
      with every model registered against ``Base.metadata`` so all FKs
      resolve correctly.
    - ``async_session`` — function-scoped AsyncSession bound to that
      engine; tables are created and dropped around each test so
      tests are isolated.

The fixtures hit a lightweight in-memory SQLite DB rather than the
project's PostgreSQL — that keeps the test suite fast and lets it run
without a running Docker stack. Tests that need PostgreSQL-only
features (JSONB queries, native ENUM ordering, etc.) should mark
themselves with ``@pytest.mark.postgres`` and be skipped here.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ── Model registry — every model must be imported at least once so it
#    attaches to Base.metadata before create_all() runs. The block
#    mirrors the import block in app.main exactly.
from app.modules.auth.models import User                                # noqa: F401
from app.modules.programs.models import AcademicProgram                 # noqa: F401
from app.shared.audit.models import SystemAuditLog                      # noqa: F401
from app.modules.undergraduate.models import (                          # noqa: F401
    UndergraduateAdmissionTerm, UndergraduateApplication,
    ApplicationDocument, ApplicationStatusHistory, RegistrarDecision,
)
from app.ai.models import AIEvaluation, AIExecutionTrace                # noqa: F401
from app.modules.moe.models import MoeStudentRecord                     # noqa: F401
from app.modules.testing_center.models import UATRecord                 # noqa: F401
from app.modules.undergraduate.ranking.models import (                  # noqa: F401
    StreamQuota, RankingResult,
)
from app.modules.undergraduate.enrollment.models import Enrollment      # noqa: F401
from app.modules.course.models import (                                 # noqa: F401
    AcademicTerm, Course, CoursePrerequisite, Section, ClassScheduleSlot,
    Classroom,
    Student, Instructor, InstructorAssignment, CourseManagementOfficer,
    Registration, RegistrationCourse, RegistrationStatusHistory,
    AddDropRequest, AdvisoryRecommendation, PrerequisiteOverride,
    ScheduleConflict,
)
from app.modules.course.grading.models import (                         # noqa: F401
    AssessmentBreakdown, AssessmentComponent, GradeAgentReview,
    GradeAuthorisationDecision, GradeBatch, StudentComponentScore,
)
from app.modules.course.standing.models import (                        # noqa: F401
    AcademicStanding, AcademicStandingHistory,
)

from app.database.base import Base


# ── pytest-asyncio in auto mode means async test functions don't need
#    to be individually decorated with @pytest.mark.asyncio.
pytest_plugins = ("pytest_asyncio",)


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest_asyncio.fixture(scope="function")
async def async_engine():
    """A fresh in-memory SQLite engine per test."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        future=True,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def async_session(async_engine) -> AsyncGenerator[AsyncSession, None]:
    """A fresh AsyncSession per test, with foreign keys enabled."""
    sessionmaker = async_sessionmaker(
        bind=async_engine, class_=AsyncSession, expire_on_commit=False,
    )
    async with sessionmaker() as session:
        # SQLite needs explicit FK enforcement for our CHECK and FK
        # constraint tests to behave like PostgreSQL.
        await session.execute(
            __import__("sqlalchemy").text("PRAGMA foreign_keys = ON")
        )
        yield session
