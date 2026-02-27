"""
AI Integration Layer — placeholder.

This package is the future home of all AI-related components:

    app/ai/
    ├── __init__.py
    ├── base.py           ← Abstract agent interface
    ├── tracing.py         ← Decision trace schemas
    ├── workflows/         ← LangGraph workflow definitions (future)
    │   ├── undergraduate_eval.py
    │   └── graduate_eval.py
    ├── prompts/           ← Prompt templates for agents (future)
    ├── evaluators/        ← Evaluation pipelines (future)
    └── tools/             ← Agent tools wrapping module services (future)

Integration pattern:
    - Agents use the SAME service layer as HTTP routes
    - Decision traces are persisted via shared/audit/
    - Human-in-the-loop via registrar approval checkpoints
"""
