from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class UndergradAdmissionSettings(BaseSettings):
    """Configuration for the undergrad admission service."""

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)

    environment: str = Field(default="local", validation_alias="ENVIRONMENT")

    # Database
    db_host: str = Field(default="localhost", validation_alias="POSTGRES_HOST")
    db_port: int = Field(default=5432, validation_alias="POSTGRES_PORT")
    db_name: str = Field(default="agentic_registrar", validation_alias="POSTGRES_DB")
    db_user: str = Field(default="agentic_registrar", validation_alias="POSTGRES_USER")
    db_password: str = Field(default="changeme", validation_alias="POSTGRES_PASSWORD")

    # Policy configuration
    score_cutoff: float = Field(default=50.0, validation_alias="UNDERGRAD_SCORE_CUTOFF")
    uat_weight: float = Field(default=0.6, validation_alias="UNDERGRAD_UAT_WEIGHT")
    cgpa_weight: float = Field(default=0.4, validation_alias="UNDERGRAD_CGPA_WEIGHT")

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


@lru_cache(maxsize=1)
def get_settings() -> UndergradAdmissionSettings:
    return UndergradAdmissionSettings()

