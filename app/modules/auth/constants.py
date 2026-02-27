"""
Auth module constants — role definitions.

NOTE: Roles should be moved to app/shared/enums/ once that package is
implemented. This file exists as a reference until then.

Roles are stored as strings in the database to allow easy extension
when new modules (e.g., department heads) are added later.
"""


class UserRole:
    """User role constants — to be migrated to shared/enums."""

    STUDENT = "STUDENT"
    REGISTRAR_OFFICER = "REGISTRAR_OFFICER"
    ADMIN = "ADMIN"

    ALL = {STUDENT, REGISTRAR_OFFICER, ADMIN}
