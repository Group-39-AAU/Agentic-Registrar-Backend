"""
Shared base class for every Course Management agent.

Adds one course-management-specific extension to :class:`BaseAgent`: a
policy-grounding hook reserved for the pgvector-backed Retrieval-
Augmented Generation flow described in SRS §3.7 ("Vectorized Policy
Knowledge Base").

In Phase 1 the hook is a no-op so rule-based agents work without the
RAG infrastructure online. In Phase 2 the hook will query the policy
embeddings table and return matching AAU senate-legislation snippets
that downstream LangGraph nodes feed to the underlying LLM as context.
"""

from typing import Any

from app.ai.base import BaseAgent
from app.shared.enums import AgentStatus


class CourseBaseAgent(BaseAgent):
    """
    Base class shared by every agent in the Course Management module.

    Concrete subclasses still implement :meth:`process_task` from
    :class:`BaseAgent`. They may additionally call
    :meth:`ground_in_policy` to retrieve relevant policy excerpts.
    """

    def __init__(
        self,
        agent_id: str,
        status: AgentStatus = AgentStatus.IDLE,
    ) -> None:
        super().__init__(agent_id=agent_id, status=status)

    async def ground_in_policy(self, query: str) -> list[str]:
        """
        Retrieve AAU policy snippets relevant to ``query``.

        Phase 1: returns an empty list — the rule-based agents have all
        the policy they need hard-coded in their RuleSet attributes.

        Phase 2: queries the pgvector ``policy_documents`` table per
        SRS §3.7 and returns the top-k matching snippets, ordered by
        cosine similarity.
        """
        _ = query  # placeholder until pgvector integration lands
        return []

    async def process_task(
        self, input_data: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Default no-op implementation so the base class is concrete enough
        to instantiate in tests. Concrete agents override this method.
        """
        raise NotImplementedError(
            "Concrete CourseBaseAgent subclasses must override process_task."
        )
