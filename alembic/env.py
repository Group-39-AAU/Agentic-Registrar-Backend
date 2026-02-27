"""
Alembic environment configuration for async SQLAlchemy migrations.

TODO: Implement:
    - Import all models from modules so Alembic can detect them
    - Set target_metadata = Base.metadata
    - Override sqlalchemy.url with settings.DATABASE_URL
    - run_migrations_offline() — generates SQL without DB connection
    - run_migrations_online() — async migration runner using asyncpg
"""

import sys
from pathlib import Path

# Ensure the project root is on sys.path so 'app' is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
