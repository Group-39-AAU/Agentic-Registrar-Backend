"""
Course Management — domain exception classes.

Mirrors app/modules/undergraduate/exceptions.py. Service layer raises
these; the router maps them to HTTP responses.
"""


class InvalidStateTransitionError(Exception):
    """Raised when an illegal RegistrationStatus transition is attempted."""

    def __init__(self, current: str, target: str) -> None:
        self.current = current
        self.target = target
        super().__init__(f"Cannot transition registration from {current} to {target}")


class RegistrationWindowClosedError(Exception):
    """Raised when a student tries to register against a closed term."""

    def __init__(self, term_name: str) -> None:
        self.term_name = term_name
        super().__init__(
            f"Registration window for term '{term_name}' is closed."
        )


class DuplicateRegistrationError(Exception):
    """Raised when a student already has a registration for a term."""

    def __init__(self, student_id: str, term_id: str) -> None:
        self.student_id = student_id
        self.term_id = term_id
        super().__init__(
            f"Student {student_id} already has a registration for term {term_id}"
        )


class ComplianceCheckFailedError(Exception):
    """
    Raised when the CurriculumComplianceAgent blocks a registration.
    Carries the structured agent payload so the router can surface
    plain-language reasons to the student.
    """

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        super().__init__("Registration failed compliance checks.")


class UnauthorizedActorError(Exception):
    """Raised when the calling user lacks the required role."""

    def __init__(self, detail: str = "Insufficient permissions") -> None:
        self.detail = detail
        super().__init__(detail)


class EntityNotFoundError(Exception):
    """Raised when a requested entity does not exist."""

    def __init__(self, entity: str, entity_id: str) -> None:
        self.entity = entity
        self.entity_id = entity_id
        super().__init__(f"{entity} with id {entity_id} not found")


class AdjustmentDeniedError(Exception):
    """
    Raised by AddDropService.submit_request when the
    EnrollmentAdjustmentAgent blocks the request. Carries the
    structured agent payload so the router can show plain-language
    reasons against the request.
    """

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        super().__init__("Add/drop request was denied by the adjustment agent.")


class InvalidAdjustmentRequestError(Exception):
    """Raised when an add/drop request is malformed for its target state."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)
