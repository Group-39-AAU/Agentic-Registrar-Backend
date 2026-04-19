"""
Alembic environment configuration for async SQLAlchemy migrations.
"""

import sys
from pathlib import Path

# Ensure the project root is on sys.path so 'app' is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

from app.core.config import settings
from app.database.base import Base

# ── Import ALL models so Alembic's autogenerate can detect them ──
from app.modules.auth.models import User                          # noqa: F401
from app.modules.programs.models import AcademicProgram            # noqa: F401
from app.modules.undergraduate.models import (                    # noqa: F401
    UndergraduateApplication,
    ApplicationDocument,
    ApplicationStatusHistory,
    RegistrarDecision,
)
from app.shared.audit.models import SystemAuditLog                 # noqa: F401
from app.ai.models import AIEvaluation, AIExecutionTrace           # noqa: F401

# ── Alembic Config ───────────────────────────────────────────

config = context.config
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


# ── Migration Runners ────────────────────────────────────────

def run_migrations_offline() -> None:
    """Generate SQL without a live DB connection."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations using an async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point for online (async) migrations."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
