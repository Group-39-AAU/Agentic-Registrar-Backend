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
    PAYMENT_PENDING = "PAYMENT_PENDING"
    PAYMENT_VERIFIED = "PAYMENT_VERIFIED"
    UNDER_VERIFICATION = "UNDER_VERIFICATION"
    AI_PRE_SCREENING = "AI_PRE_SCREENING"
    UAT_PENDING = "UAT_PENDING"
    UAT_COMPLETED = "UAT_COMPLETED"
    FLAGGED_FOR_REVIEW = "FLAGGED_FOR_REVIEW"
    PENDING_REVIEW = "PENDING_REVIEW"
    DECIDED = "DECIDED"
    ENROLLED = "ENROLLED"


class SponsorshipType(str, Enum):
    """Student funding source."""
    GOVERNMENT = "GOVERNMENT"
    SELF_SPONSORED = "SELF_SPONSORED"


class StreamType(str, Enum):
    """Academic stream for 12th-grade students."""
    NATURAL = "NATURAL"
    SOCIAL = "SOCIAL"


class PaymentStatus(str, Enum):
    """Application payment lifecycle."""
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class DocumentType(str, Enum):
    """Types of documents an applicant may submit."""
    TRANSCRIPT = "TRANSCRIPT"
    GRADE_12_CERTIFICATE = "GRADE_12_CERTIFICATE"
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


# ══════════════════════════════════════════════════════════════
#  Course Management — Foundation Enums
# ══════════════════════════════════════════════════════════════


class AgentStatus(str, Enum):
    """
    Lifecycle state of every agent that extends BaseAgent.
    Casing matches SDS Table 86 verbatim.
    """
    IDLE = "IDLE"
    BUSY = "BUSY"
    WAITING_HUMAN = "WAITING_HUMAN"
    ERROR = "ERROR"


class OfficerRole(str, Enum):
    """
    Role discriminator for CourseManagementOfficer (SDS Table 62).
    Only DEPARTMENT_HEAD may override unmet prerequisites per SRS §3.5
    inverse requirement.
    """
    REGISTRAR_OFFICER = "REGISTRAR_OFFICER"
    DEPARTMENT_HEAD = "DEPARTMENT_HEAD"


class RiskStatus(str, Enum):
    """
    Returned by AcademicAdvisoryAgent.flagRiskLevel (SDS Table 69).
    HIGH escalates to a mandatory CourseManagementOfficer review.
    """
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class RegistrationStatus(str, Enum):
    """
    Per-student-per-term registration lifecycle.
    Mirrors the state diagram in SDS Figure 39 verbatim, so every
    state in the implemented state machine is traceable to the design.

    Flow:
        REGISTRATION_OPEN
            -> ADVISOR_REVIEW           (student selects courses)
            -> CHECKING_PREREQUISITES   (CurriculumComplianceAgent)
            -> CHECKING_PAYMENT
                -> VALIDATION_SUCCESS   (payment confirmed)
                -> PAYMENT_HOLD         (payment missing)
            -> REGISTERED               (CurriculumComplianceAgent finalises)
            -> ADD_DROP_WINDOW          (late registration period)
        CANCELLED is a terminal state reachable from any non-terminal state.
    """
    REGISTRATION_OPEN = "REGISTRATION_OPEN"
    ADVISOR_REVIEW = "ADVISOR_REVIEW"
    CHECKING_PREREQUISITES = "CHECKING_PREREQUISITES"
    CHECKING_PAYMENT = "CHECKING_PAYMENT"
    PAYMENT_HOLD = "PAYMENT_HOLD"
    VALIDATION_SUCCESS = "VALIDATION_SUCCESS"
    REGISTERED = "REGISTERED"
    ADD_DROP_WINDOW = "ADD_DROP_WINDOW"
    CANCELLED = "CANCELLED"


class GradeLetter(str, Enum):
    """
    AAU letter-grade scale used by Track B (grading lifecycle).
    Includes the special non-numeric marks I (Incomplete) and NG
    (No Grade) that route an academic-status calculation through
    AcademicStandingAgent.handleEdgeCase per SDS Table 75.
    """
    A = "A"
    A_MINUS = "A-"
    B_PLUS = "B+"
    B = "B"
    B_MINUS = "B-"
    C_PLUS = "C+"
    C = "C"
    C_MINUS = "C-"
    D = "D"
    F = "F"
    I = "I"   # Incomplete
    NG = "NG"  # No Grade


class GradeSubmissionStatus(str, Enum):
    """Per-section grade-batch lifecycle (Track B)."""
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    FLAGGED = "FLAGGED"
    AUTHORISED = "AUTHORISED"
    REJECTED = "REJECTED"


class AddDropAction(str, Enum):
    """
    Pre-condition discriminator on
    EnrollmentAdjustmentAgent.updateSectionCapacity (SDS Table 81).
    """
    ADD = "ADD"
    DROP = "DROP"


class AddDropRequestStatus(str, Enum):
    """
    Lifecycle of an AddDropRequest from submission to applied change.

    Flow:
        PENDING                      (student just submitted)
            -> APPROVED              (EnrollmentAdjustmentAgent passed)
            -> DENIED                (agent blocked it)
            -> OVERRIDDEN            (officer override of a DENIED request)
        APPROVED | OVERRIDDEN
            -> APPLIED               (change written to the registration)
    """
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    OVERRIDDEN = "OVERRIDDEN"
    APPLIED = "APPLIED"


class AcademicStatusType(str, Enum):
    """
    Per-term academic standing assigned by AcademicStandingAgent
    (SDS Table 75) and authorised by the CourseManagementOfficer.

    Threshold invariants from SDS Table 74:
        WARNING when CGPA < 2.0
        DISTINCTION when CGPA > 3.5
    """
    PROMOTED = "PROMOTED"
    WARNING = "WARNING"
    DISTINCTION = "DISTINCTION"
    DISMISSED = "DISMISSED"
    INCOMPLETE = "INCOMPLETE"


class EnrollmentStatus(str, Enum):
    """
    Per-student lifecycle on the Student entity (SDS Table 56).
    Distinct from AcademicStatusType, which is per-term.
    """
    ACTIVE = "ACTIVE"
    DISMISSED = "DISMISSED"
    WITHDRAWN = "WITHDRAWN"
    GRADUATED = "GRADUATED"


class ExceptionStatus(str, Enum):
    """Track C unified exception-queue lifecycle."""
    OPEN = "OPEN"
    IN_REVIEW = "IN_REVIEW"
    RESOLVED = "RESOLVED"
    ESCALATED = "ESCALATED"
