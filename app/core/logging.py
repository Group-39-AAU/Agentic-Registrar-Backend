"""
Structured logging configuration.

TODO: Implement:
    - JSONFormatter (logging.Formatter subclass) — JSON output per log line
    - setup_logging(log_level) — configure root logger, call once at startup
    - get_logger(name) — returns named logger (e.g., get_logger("undergraduate"))
    - audit_log(action, actor_id, actor_role, resource_type, resource_id,
                decision, metadata)
        - Emits structured audit entries to 'app.audit' logger
        - Includes audit_id (UUID), timestamp, and all parameters
        - Future: also persist to audit_logs DB table

Audit logging is critical for registrar systems — every application status
change, approval, and rejection must be logged with who did it and why.
The metadata field stores arbitrary context (agent reasoning goes here later).
"""
