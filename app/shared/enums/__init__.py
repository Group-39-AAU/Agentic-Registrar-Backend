"""
Centralized Enums — placeholder.

All status enums, decision enums, and role enums live here to prevent
enum drift across modules. Agents and services import from this
single source of truth.

TODO: Implement the following enums:

    class ApplicationStatus(str, Enum):
        PENDING = "PENDING"
        UNDER_REVIEW = "UNDER_REVIEW"
        APPROVED = "APPROVED"
        REJECTED = "REJECTED"

    class DecisionType(str, Enum):
        RECOMMEND_APPROVE = "RECOMMEND_APPROVE"
        RECOMMEND_REJECT = "RECOMMEND_REJECT"
        HUMAN_APPROVED = "HUMAN_APPROVED"
        HUMAN_REJECTED = "HUMAN_REJECTED"

    class UserRole(str, Enum):
        STUDENT = "STUDENT"
        REGISTRAR_OFFICER = "REGISTRAR_OFFICER"
        ADMIN = "ADMIN"

    class DegreeLevel(str, Enum):
        BACHELORS = "BACHELORS"
        MASTERS = "MASTERS"
        PHD = "PHD"

    class EnrollmentStatus(str, Enum):
        ENROLLED = "ENROLLED"
        DROPPED = "DROPPED"
        COMPLETED = "COMPLETED"
"""
