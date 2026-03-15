from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


Environment = Literal["local", "dev", "staging", "prod"]


class DatabaseSettings(BaseModel):
    host: str = "localhost"
    port: int = 5432
    name: str = "agentic_registrar"
    user: str = "agentic_registrar"
    password: str = "changeme"


class MinioSettings(BaseModel):
    endpoint: str = "localhost:9000"
    access_key: str = "agentic-registrar"
    secret_key: str = "changeme-minio"
    documents_bucket: str = "agentic-registrar-documents"
    secure: bool = False


class JWTSettings(BaseModel):
    secret: str = "change_me_in_production"
    algorithm: str = "HS256"
    access_token_expires_minutes: int = 60


class AppSettings(BaseSettings):
    """Application-wide configuration loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)

    environment: Environment = Field(default="local", validation_alias="ENVIRONMENT")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    database: DatabaseSettings = DatabaseSettings()
    minio: MinioSettings = MinioSettings()
    jwt: JWTSettings = JWTSettings()


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Load and cache application settings.

    This is safe to call from within services; it will only hit the environment once.
    """

    return AppSettings()  # type: ignore[call-arg]

