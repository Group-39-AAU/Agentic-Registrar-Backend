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

    # Public base URL for links in emails (scheme + host, no trailing slash).
    # Example: https://api.university.edu or http://localhost:8000
    PUBLIC_APP_BASE_URL: str = "http://localhost:8000"

    # ── Database ─────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/registrar_db"

    # ── Authentication ───────────────────────────────────
    SECRET_KEY: str = "change-me-to-a-random-secret-key"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    ALGORITHM: str = "HS256"

    # ── AI / Gemini ──────────────────────────────────────
    GEMINI_API_KEY: str = ""

    # ── AI / Anthropic (advisory narrative LLM) ──────────
    # Empty key disables LLM enrichment; AcademicAdvisoryAgent then
    # falls back to its rule-based explanation (see app/ai/llm_client.py).
    ANTHROPIC_API_KEY: str = ""
    ADVISORY_LLM_MODEL: str = "claude-haiku-4-5"
    ADVISORY_LLM_TIMEOUT_SECONDS: float = 5.0
    ADVISORY_LLM_MAX_TOKENS: int = 600

    # ── Logging ──────────────────────────────────────────
    LOG_LEVEL: str = "INFO"

    # ── Email (Brevo) ────────────────────────────────────
    EMAIL_ENABLED: bool = True
    BREVO_API_KEY: str = ""
    EMAIL_FROM: str = ""
    EMAIL_FROM_NAME: str = "Agentic Registrar"
    EMAIL_TIMEOUT_SECONDS: int = 15


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (singleton pattern)."""
    return Settings()


settings: Settings = get_settings()
