from __future__ import annotations

from enum import Enum, StrEnum


class Environment(StrEnum):
    LOCAL = "local"
    DEV = "dev"
    STAGING = "staging"
    PROD = "prod"


class SortOrder(StrEnum):
    ASC = "asc"
    DESC = "desc"


class AuditAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    ACCESS = "access"


class AuthTokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


class AcademicStanding(str, Enum):
    GOOD = "good"
    PROBATION = "probation"
    SUSPENDED = "suspended"
    DISMISSED = "dismissed"

