from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DocumentSettings(BaseSettings):
    """Configuration for the document service."""

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)

    environment: str = Field(default="local", validation_alias="ENVIRONMENT")

    # Database
    db_host: str = Field(default="localhost", validation_alias="POSTGRES_HOST")
    db_port: int = Field(default=5432, validation_alias="POSTGRES_PORT")
    db_name: str = Field(default="agentic_registrar", validation_alias="POSTGRES_DB")
    db_user: str = Field(default="agentic_registrar", validation_alias="POSTGRES_USER")
    db_password: str = Field(default="changeme", validation_alias="POSTGRES_PASSWORD")

    # Object storage
    minio_endpoint: str = Field(default="minio:9000", validation_alias="MINIO_ENDPOINT")
    minio_root_user: str = Field(
        default="agentic-registrar",
        validation_alias="MINIO_ROOT_USER",
    )
    minio_root_password: str = Field(
        default="changeme-minio",
        validation_alias="MINIO_ROOT_PASSWORD",
    )
    minio_bucket_documents: str = Field(
        default="agentic-registrar-documents",
        validation_alias="MINIO_BUCKET_DOCUMENTS",
    )

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


@lru_cache(maxsize=1)
def get_settings() -> DocumentSettings:
    return DocumentSettings()

