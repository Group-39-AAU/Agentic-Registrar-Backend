"""
Audit Infrastructure — placeholder.

Registrar systems MUST be auditable. This package will hold:

    - Audit SQLAlchemy model (audit_logs table)
        - id (UUID)
        - timestamp
        - actor_id, actor_role
        - action (e.g., "application.approved")
        - resource_type, resource_id
        - decision
        - metadata (JSONB — stores agent reasoning, context, etc.)

    - Audit service / logging adapter
        - audit_log() function that writes to both:
            1. Structured log output (JSON)
            2. audit_logs database table

    - Actor tracking helpers
        - Extract actor info from request context
        - Support system-level actions (no actor)
        - Support AI agent actions (agent as actor)

This aligns with SRS compliance requirements for the Agentic Registrar System.
"""
