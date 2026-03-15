from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class GraduateAdmissionSettings(BaseSettings):
    """Configuration for the graduate admission service."""

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)

    environment: str = Field(default="local", validation_alias="ENVIRONMENT")

    # Database
    db_host: str = Field(default="localhost", validation_alias="POSTGRES_HOST")
    db_port: int = Field(default=5432, validation_alias="POSTGRES_PORT")
    db_name: str = Field(default="agentic_registrar", validation_alias="POSTGRES_DB")
    db_user: str = Field(default="agentic_registrar", validation_alias="POSTGRES_USER")
    db_password: str = Field(default="changeme", validation_alias="POSTGRES_PASSWORD")

    # Policy configuration
    gat_cutoff: float = Field(default=50.0, validation_alias="GRAD_GAT_CUTOFF")
    transcript_weight: float = Field(default=0.5, validation_alias="GRAD_TRANSCRIPT_WEIGHT")
    gat_weight: float = Field(default=0.5, validation_alias="GRAD_GAT_WEIGHT")
    department_capacity: int = Field(default=30, validation_alias="GRAD_DEPARTMENT_CAPACITY")

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


@lru_cache(maxsize=1)
def get_settings() -> GraduateAdmissionSettings:
    return GraduateAdmissionSettings()

