"""
Centralized application configuration.

Loads settings from environment variables (or .env file) using pydantic-settings.
Access the singleton `settings` instance from anywhere:

    from app.core.config import settings
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── App ──────────────────────────────────────────────
    APP_NAME: str = "Agentic Registrar System"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"  # development | testing | production
    API_V1_PREFIX: str = "/api/v1"

    # ── Database ─────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/registrar_db"

    # ── Authentication ───────────────────────────────────
    SECRET_KEY: str = "change-me-to-a-random-secret-key"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    ALGORITHM: str = "HS256"

    # ── AI / Gemini ──────────────────────────────────────
    GEMINI_API_KEY: str = ""

    # ── Logging ──────────────────────────────────────────
    LOG_LEVEL: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (singleton pattern)."""
    return Settings()


settings: Settings = get_settings()
