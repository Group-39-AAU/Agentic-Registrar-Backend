"""
Centralized Enums for the Agentic Registrar System.

All status, decision, and role enums live here as the single source of truth.
They map to native PostgreSQL ENUM types via SQLAlchemy.
"""

from enum import Enum


class ApplicationStatus(str, Enum):
    """Undergraduate admission lifecycle states."""
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    UNDER_VERIFICATION = "UNDER_VERIFICATION"
    AI_PRE_SCREENING = "AI_PRE_SCREENING"
    PENDING_REVIEW = "PENDING_REVIEW"
    DECIDED = "DECIDED"


class DocumentType(str, Enum):
    """Types of documents an applicant may submit."""
    TRANSCRIPT = "TRANSCRIPT"
    ID_CARD = "ID_CARD"
    CERTIFICATE = "CERTIFICATE"
    RECOMMENDATION_LETTER = "RECOMMENDATION_LETTER"
    STATEMENT_OF_PURPOSE = "STATEMENT_OF_PURPOSE"


class VerificationStatus(str, Enum):
    """Document verification workflow states."""
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"


class DecisionType(str, Enum):
    """AI recommendations and human final decisions."""
    # AI recommendations
    RECOMMEND_ADMIT = "RECOMMEND_ADMIT"
    RECOMMEND_REJECT = "RECOMMEND_REJECT"
    RECOMMEND_WAITLIST = "RECOMMEND_WAITLIST"
    FLAG_FOR_REVIEW = "FLAG_FOR_REVIEW"
    # Human final decisions
    ADMIT = "ADMIT"
    REJECT = "REJECT"
    WAITLIST = "WAITLIST"


class UserRole(str, Enum):
    """System-wide user roles."""
    STUDENT = "STUDENT"
    REGISTRAR_OFFICER = "REGISTRAR_OFFICER"
    ADMIN = "ADMIN"
    SYSTEM = "SYSTEM"   # Automated background jobs
    AGENT = "AGENT"     # LangGraph AI agents
