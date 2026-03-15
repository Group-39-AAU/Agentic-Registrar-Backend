from __future__ import annotations

from functools import lru_cache

from pydantic import AnyHttpUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class GatewaySettings(BaseSettings):
    """Configuration for the API gateway."""

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)

    environment: str = Field(default="local", validation_alias="ENVIRONMENT")

    identity_base_url: AnyHttpUrl = Field(
        default="http://identity-service:8000",
        validation_alias="IDENTITY_SERVICE_URL",
    )
    undergrad_base_url: AnyHttpUrl = Field(
        default="http://undergrad-admission-service:8000",
        validation_alias="UNDERGRAD_ADMISSION_SERVICE_URL",
    )
    graduate_base_url: AnyHttpUrl = Field(
        default="http://graduate-admission-service:8000",
        validation_alias="GRADUATE_ADMISSION_SERVICE_URL",
    )
    courses_base_url: AnyHttpUrl = Field(
        default="http://course-management-service:8000",
        validation_alias="COURSE_MANAGEMENT_SERVICE_URL",
    )
    documents_base_url: AnyHttpUrl = Field(
        default="http://document-service:8000",
        validation_alias="DOCUMENT_SERVICE_URL",
    )
    notifications_base_url: AnyHttpUrl = Field(
        default="http://notification-service:8000",
        validation_alias="NOTIFICATION_SERVICE_URL",
    )


@lru_cache(maxsize=1)
def get_settings() -> GatewaySettings:
    return GatewaySettings()

