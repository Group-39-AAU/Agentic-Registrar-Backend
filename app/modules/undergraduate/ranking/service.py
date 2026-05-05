import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.undergraduate.ranking.models import RankingResult


class RankingService:
    """Service layer for ranking orchestration and rerun policies."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_next_run_number(self, term_id: uuid.UUID) -> int:
        latest_run = (await self._db.execute(
            select(func.max(RankingResult.ranking_run_number)).where(
                RankingResult.admission_term_id == term_id
            )
        )).scalar()
        return (latest_run or 0) + 1

    async def apply_first_run_cutoffs_for_rerun(
        self,
        *,
        term_id: uuid.UUID,
        final_state: dict[str, Any],
        program_info: dict[str, dict[str, str]],
    ) -> None:
        """
        Re-assign rerun applicants by using first-run cutoffs for the term.
        Mutates applicants in `final_state` in-place.
        """
        first_run_rows = (await self._db.execute(
            select(RankingResult).where(
                RankingResult.admission_term_id == term_id,
                RankingResult.ranking_run_number == 1,
            )
        )).scalars().all()
        if not first_run_rows:
            raise ValueError("First run ranking results are required for rerun cutoffs")

        program_cutoffs: dict[uuid.UUID, float] = {}
        stream_cutoffs: dict[str, float] = {}

        for row in first_run_rows:
            if row.is_assigned and row.assigned_program_id:
                current_cutoff = program_cutoffs.get(row.assigned_program_id)
                if current_cutoff is None or row.final_score < current_cutoff:
                    program_cutoffs[row.assigned_program_id] = row.final_score

            if row.is_assigned and row.assigned_stream:
                stream_key = row.assigned_stream.value
                current_cutoff = stream_cutoffs.get(stream_key)
                if current_cutoff is None or row.final_score < current_cutoff:
                    stream_cutoffs[stream_key] = row.final_score

        for applicant in final_state["self_sponsored"]:
            applicant.is_assigned = False
            applicant.assigned_program_id = None
            applicant.assignment_detail = "Unassigned — below first-run cutoffs"

            for choice_num, prog_id in enumerate(
                [applicant.program_choice_1_id, applicant.program_choice_2_id, applicant.program_choice_3_id], 1
            ):
                if prog_id is None:
                    continue
                cutoff = program_cutoffs.get(prog_id)
                if cutoff is not None and applicant.final_score >= cutoff:
                    applicant.is_assigned = True
                    applicant.assigned_program_id = prog_id
                    prog = program_info.get(str(prog_id), {})
                    prog_name = prog.get("name", str(prog_id))
                    applicant.assignment_detail = (
                        f"Assigned by first-run cutoff to P{choice_num}: {prog_name} "
                        f"(cutoff={cutoff})"
                    )
                    break

        for applicant in final_state["government"]:
            applicant.is_assigned = False
            applicant.assigned_stream = None
            cutoff = stream_cutoffs.get(applicant.stream)
            if cutoff is not None and applicant.final_score >= cutoff:
                applicant.is_assigned = True
                applicant.assigned_stream = applicant.stream
                applicant.assignment_detail = (
                    f"Assigned by first-run stream cutoff: {applicant.stream} (cutoff={cutoff})"
                )
            else:
                applicant.assignment_detail = "Unassigned — below first-run stream cutoff"
