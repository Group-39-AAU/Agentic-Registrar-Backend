"""
Custom exception classes and FastAPI exception handlers.

TODO: Implement:
    - AppException (base, with detail + status_code)
    - EntityNotFoundError(entity, entity_id) -> 404
    - DuplicateEntityError(entity, field, value) -> 409
    - AuthorizationError(detail) -> 403
    - register_exception_handlers(app) -> registers handlers on the FastAPI app
"""
