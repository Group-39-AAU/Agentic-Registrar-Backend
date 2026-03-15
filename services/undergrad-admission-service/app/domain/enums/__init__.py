from __future__ import annotations

from enum import StrEnum


class ApplicationStatus(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    VALIDATION_PENDING = "validation_pending"
    VALIDATED = "validated"
    VERIFICATION_PENDING = "verification_pending"
    VERIFIED = "verified"
    FLAGGED_FOR_REVIEW = "flagged_for_review"
    RANKED = "ranked"
    RECOMMENDED = "recommended"
    APPROVED = "approved"
    REJECTED = "rejected"
    ONBOARDED = "onboarded"


class VerificationOutcome(StrEnum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"


class Channel(StrEnum):
    EMAIL = "email"
    SMS = "sms"

