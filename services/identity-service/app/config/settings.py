from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class IdentitySettings(BaseSettings):
    """Configuration for the identity service."""

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)

    environment: str = Field(default="local", validation_alias="ENVIRONMENT")

    # Database
    db_host: str = Field(default="localhost", validation_alias="POSTGRES_HOST")
    db_port: int = Field(default=5432, validation_alias="POSTGRES_PORT")
    db_name: str = Field(default="agentic_registrar", validation_alias="POSTGRES_DB")
    db_user: str = Field(default="agentic_registrar", validation_alias="POSTGRES_USER")
    db_password: str = Field(default="changeme", validation_alias="POSTGRES_PASSWORD")

    # JWT
    jwt_secret: str = Field(default="change_me_in_production", validation_alias="JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", validation_alias="JWT_ALGORITHM")
    jwt_access_expires_minutes: int = Field(
        default=60,
        validation_alias="JWT_ACCESS_TOKEN_EXPIRES_MINUTES",
    )

    # Optional admin bootstrap
    admin_username: str | None = Field(default=None, validation_alias="IDENTITY_ADMIN_USERNAME")
    admin_password: str | None = Field(default=None, validation_alias="IDENTITY_ADMIN_PASSWORD")
    admin_email: str | None = Field(default=None, validation_alias="IDENTITY_ADMIN_EMAIL")

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


@lru_cache(maxsize=1)
def get_settings() -> IdentitySettings:
    return IdentitySettings()

