from __future__ import annotations

from enum import StrEnum


class ApplicationStatus(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    VALIDATION_PENDING = "validation_pending"
    VALIDATED = "validated"
    GAT_VERIFIED = "gat_verified"
    TRANSCRIPT_VERIFICATION_PENDING = "transcript_verification_pending"
    TRANSCRIPT_VERIFIED = "transcript_verified"
    ROUTED_TO_DEPARTMENT = "routed_to_department"
    DEPARTMENT_REVIEW = "department_review"
    DEPARTMENT_APPROVED = "department_approved"
    REGISTRAR_REVIEW = "registrar_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    ENROLLED = "enrolled"


class VerificationOutcome(StrEnum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"

