"""
Domain Events — placeholder.

Registrar systems are event-heavy. This package will hold domain event
definitions that decouple modules and enable:

    - Notification triggers (e.g., email on status change)
    - AI agent triggers (e.g., agent evaluation after submission)
    - Audit log automation

Example events to be defined here:
    - ApplicationSubmitted
    - ApplicationStatusChanged
    - AgentRecommendationGenerated
    - RegistrarApproved
    - RegistrarRejected
    - CourseEnrollmentCreated

Each event should be a Pydantic model containing:
    - event_id (UUID)
    - event_type (str)
    - timestamp (datetime)
    - actor_id (UUID)
    - actor_role (str)
    - payload (dict)
"""
