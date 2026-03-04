"""
AI Evaluation and Execution Trace models.
"""

import uuid
from typing import Optional

from sqlalchemy import Boolean, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.shared.enums import DecisionType


class AIEvaluation(Base):
    """
    Stores the result of an AI agent's read-only assessment of an application.
    Inherits from Base — immutable once written. AI never overwrites human decisions.
    """
    __tablename__ = "ai_evaluations"

    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("undergraduate_applications.id"),
        index=True, nullable=False,
    )
    agent_version: Mapped[str] = mapped_column(String(100), nullable=False)
    recommended_decision: Mapped[DecisionType] = mapped_column(nullable=False)
    confidence_score: Mapped[float] = mapped_column(
        Float, nullable=False
    )  # 0.0 – 1.0
    is_overridden: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    summary_reasoning: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Relationships ──
    traces: Mapped[list["AIExecutionTrace"]] = relationship(
        back_populates="evaluation", lazy="selectin",
        order_by="AIExecutionTrace.created_at.asc()",
    )


class AIExecutionTrace(Base):
    """
    Step-by-step reasoning storage for AI explainability.
    Inherits from Base — immutable.
    """
    __tablename__ = "ai_execution_traces"

    evaluation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_evaluations.id"),
        index=True, nullable=False,
    )
    step_name: Mapped[str] = mapped_column(String(100), nullable=False)
    reasoning_log: Mapped[str] = mapped_column(Text, nullable=False)
    rag_references: Mapped[dict] = mapped_column(
        JSONB, server_default="{}", nullable=False
    )
    token_usage: Mapped[dict] = mapped_column(
        JSONB, server_default="{}", nullable=False
    )

    # ── Relationships ──
    evaluation: Mapped["AIEvaluation"] = relationship(
        back_populates="traces", lazy="selectin"
    )
