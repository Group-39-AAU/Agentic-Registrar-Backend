# AI Agent Integration — Extension Point

> **Status**: Placeholder — not yet implemented.
> This package is the future home of AI agent orchestration.

## Architecture

```
app/agents/
├── base.py          ← Abstract agent interface (current)
├── tracing.py       ← Agent decision trace schemas (current)
├── workflows/       ← LangGraph workflow definitions (future)
│   ├── undergraduate_eval.py
│   └── graduate_eval.py
└── tools/           ← Agent tools wrapping module services (future)
    ├── application_tools.py
    └── course_tools.py
```

## Integration Pattern

Agents will:
1. Be orchestrated via **LangGraph** workflows in `workflows/`
2. Call existing **module services** through thin wrappers in `tools/`
3. Record decision traces via `tracing.py` → persisted through the audit log system
4. Support **human-in-the-loop** by pausing at registrar approval checkpoints

## Key Principle

Agents use the **same service layer** as HTTP routes. All business rules and validations
apply equally whether a human or an AI initiates the action.
