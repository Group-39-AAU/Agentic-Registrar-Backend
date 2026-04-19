"""
Agent decision tracing schemas — placeholder.

TODO: Implement Pydantic models:
    - ReasoningStep (step_number, action, input_summary, output_summary, confidence)
    - AgentDecisionTrace (agent_name, timestamp, input_summary, decision,
                          confidence, explanation, reasoning_steps, model_version)

These are stored in audit_logs.metadata (JSONB) for explainability.
"""
