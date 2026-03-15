from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(slots=True, frozen=True)
class ApplicantScore:
    applicant_id: str
    score: float


def meets_cutoff(score: float, cutoff: float) -> bool:
    """Return True if the applicant's score meets or exceeds the cutoff."""

    return score >= cutoff


def rank_applicants(applicants: Iterable[ApplicantScore]) -> list[ApplicantScore]:
    """Return applicants sorted deterministically by score (descending) then id."""

    return sorted(applicants, key=lambda a: (-a.score, a.applicant_id))


def top_n_applicants(applicants: Sequence[ApplicantScore], n: int) -> list[ApplicantScore]:
    """Return the top N applicants by score."""

    if n <= 0:
        return []
    ranked = rank_applicants(applicants)
    return ranked[:n]

