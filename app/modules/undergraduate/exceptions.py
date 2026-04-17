"""
Undergraduate Admission module — domain exception classes.

Services raise these; routers map them to HTTP responses.
"""


class InvalidStateTransitionError(Exception):
    """Raised when an illegal workflow transition is attempted."""

    def __init__(self, current: str, target: str) -> None:
        self.current = current
        self.target = target
        super().__init__(
            f"Cannot transition from {current} to {target}"
        )


class DuplicateApplicationError(Exception):
    """Raised when a student applies to the same program/term twice."""

    def __init__(
        self,
        detail: str = "An application for this program and term already exists",
    ) -> None:
        self.detail = detail
        super().__init__(detail)


class MissingPrerequisiteError(Exception):
    """Raised when a prerequisite step hasn't been completed."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class UnauthorizedDecisionError(Exception):
    """Raised when an actor lacks the required role for an action."""

    def __init__(self, detail: str = "Insufficient permissions") -> None:
        self.detail = detail
        super().__init__(detail)


class EntityNotFoundError(Exception):
    """Raised when a requested entity does not exist."""

    def __init__(self, entity: str, entity_id: str) -> None:
        self.entity = entity
        self.entity_id = entity_id
        super().__init__(f"{entity} with id {entity_id} not found")
