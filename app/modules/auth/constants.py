"""
Auth module constants: role definitions.

Roles are stored as strings in the database to allow easy extension
when new modules (e.g., department heads) are added later.
"""


class UserRole:
    """User role constants."""

    STUDENT = "STUDENT"
    REGISTRAR_OFFICER = "REGISTRAR_OFFICER"
    ADMIN = "ADMIN"

    ALL = {STUDENT, REGISTRAR_OFFICER, ADMIN}
