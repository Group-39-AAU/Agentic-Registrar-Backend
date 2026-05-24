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

    async def apply_locked_cutoffs_for_rerun(
        self,
        *,
        term_id: uuid.UUID,
        current_run_number: int,
        final_state: dict[str, Any],
        program_info: dict[str, dict[str, str]],
        skip_application_ids: set[uuid.UUID] | None = None,
    ) -> None:
        """
        Re-assign rerun applicants by using cutoffs locked in by previous runs.

        For each program/stream, the locked cutoff is the lowest final_score from
        the EARLIEST prior run in which it received any assignments. Programs and
        streams that have never received an assignment have no floor: any applicant
        who chose them is eligible, and their assignment in this run will establish
        the cutoff going forward.

        `skip_application_ids` — applicants already locked from prior runs whose
        assignments must not be touched (the router restores them verbatim).
        """
        skip_application_ids = skip_application_ids or set()
        prior_rows = (await self._db.execute(
            select(RankingResult)
            .where(
                RankingResult.admission_term_id == term_id,
                RankingResult.ranking_run_number < current_run_number,
            )
            .order_by(RankingResult.ranking_run_number.asc())
        )).scalars().all()
        if not prior_rows:
            raise ValueError("Prior ranking results are required for rerun cutoffs")

        program_cutoffs: dict[uuid.UUID, float] = {}
        program_cutoff_run: dict[uuid.UUID, int] = {}
        stream_cutoffs: dict[str, float] = {}
        stream_cutoff_run: dict[str, int] = {}

        for row in prior_rows:
            if row.is_assigned and row.assigned_program_id:
                pid = row.assigned_program_id
                locked_run = program_cutoff_run.get(pid)
                if locked_run is None or row.ranking_run_number == locked_run:
                    program_cutoff_run.setdefault(pid, row.ranking_run_number)
                    current_cutoff = program_cutoffs.get(pid)
                    if current_cutoff is None or row.final_score < current_cutoff:
                        program_cutoffs[pid] = row.final_score

            if row.is_assigned and row.assigned_stream:
                stream_key = row.assigned_stream.value
                locked_run = stream_cutoff_run.get(stream_key)
                if locked_run is None or row.ranking_run_number == locked_run:
                    stream_cutoff_run.setdefault(stream_key, row.ranking_run_number)
                    current_cutoff = stream_cutoffs.get(stream_key)
                    if current_cutoff is None or row.final_score < current_cutoff:
                        stream_cutoffs[stream_key] = row.final_score

        for applicant in final_state["self_sponsored"]:
            if applicant.application_id in skip_application_ids:
                continue
            applicant.is_assigned = False
            applicant.assigned_program_id = None
            applicant.assignment_detail = "Unassigned — below locked cutoffs"

            for choice_num, prog_id in enumerate(
                [applicant.program_choice_1_id, applicant.program_choice_2_id, applicant.program_choice_3_id], 1
            ):
                if prog_id is None:
                    continue
                prog = program_info.get(str(prog_id), {})
                prog_name = prog.get("name", str(prog_id))
                cutoff = program_cutoffs.get(prog_id)
                if cutoff is None:
                    applicant.is_assigned = True
                    applicant.assigned_program_id = prog_id
                    applicant.assignment_detail = (
                        f"Assigned to P{choice_num}: {prog_name} "
                        f"(no prior cutoff — establishes cutoff this run)"
                    )
                    break
                if applicant.final_score >= cutoff:
                    applicant.is_assigned = True
                    applicant.assigned_program_id = prog_id
                    applicant.assignment_detail = (
                        f"Assigned by locked cutoff to P{choice_num}: {prog_name} "
                        f"(cutoff={cutoff})"
                    )
                    break

        for applicant in final_state["government"]:
            if applicant.application_id in skip_application_ids:
                continue
            applicant.is_assigned = False
            applicant.assigned_stream = None
            cutoff = stream_cutoffs.get(applicant.stream)
            if cutoff is None:
                applicant.is_assigned = True
                applicant.assigned_stream = applicant.stream
                applicant.assignment_detail = (
                    f"Assigned to stream {applicant.stream} "
                    f"(no prior cutoff — establishes cutoff this run)"
                )
            elif applicant.final_score >= cutoff:
                applicant.is_assigned = True
                applicant.assigned_stream = applicant.stream
                applicant.assignment_detail = (
                    f"Assigned by locked stream cutoff: {applicant.stream} (cutoff={cutoff})"
                )
            else:
                applicant.assignment_detail = "Unassigned — below locked stream cutoff"
