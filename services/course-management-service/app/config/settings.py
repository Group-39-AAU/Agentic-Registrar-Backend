from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CourseManagementSettings(BaseSettings):
    """Configuration for the course management service."""

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)

    environment: str = Field(default="local", validation_alias="ENVIRONMENT")

    # Database
    db_host: str = Field(default="localhost", validation_alias="POSTGRES_HOST")
    db_port: int = Field(default=5432, validation_alias="POSTGRES_PORT")
    db_name: str = Field(default="agentic_registrar", validation_alias="POSTGRES_DB")
    db_user: str = Field(default="agentic_registrar", validation_alias="POSTGRES_USER")
    db_password: str = Field(default="changeme", validation_alias="POSTGRES_PASSWORD")

    # Policy configuration
    min_credits: float = Field(default=9.0, validation_alias="COURSE_MIN_CREDITS")
    max_credits: float = Field(default=21.0, validation_alias="COURSE_MAX_CREDITS")

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


@lru_cache(maxsize=1)
def get_settings() -> CourseManagementSettings:
    return CourseManagementSettings()

