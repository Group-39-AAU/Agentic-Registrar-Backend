"""
Track B (Grading) — concrete agents.

PR 3 ships one: :class:`GradingMonitorAgent`, the LLM-as-reasoner
that substitutes the department-head's review of every submitted
grade batch.

The agent is **always** LLM-driven. Tools (in ``../tools/``) compile
deterministic statistical context; the LLM reasons over that context
as a department head and decides APPROVE / FLAG with structured
flags and a plain-English explanation. There is no rule-based
fallback — when the LLM is unavailable, the review is recorded as
PENDING for human re-trigger.
"""
from app.modules.course.grading.agents.grading_monitor_agent import (
    GradingMonitorAgent, GradingReview,
)

__all__ = ["GradingMonitorAgent", "GradingReview"]
