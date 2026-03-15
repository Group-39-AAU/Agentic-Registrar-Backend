from __future__ import annotations

from dataclasses import dataclass

from contracts.enums import AcademicStanding


@dataclass(slots=True)
class StandingThresholds:
    good: float
    probation: float
    dismissal: float


def evaluate_academic_standing(
    gpa: float,
    thresholds: StandingThresholds,
) -> AcademicStanding:
    """Evaluate a student's academic standing based on GPA thresholds.

    The rules are:
    - GPA >= thresholds.good -> GOOD
    - thresholds.probation <= GPA < thresholds.good -> PROBATION
    - thresholds.dismissal <= GPA < thresholds.probation -> SUSPENDED
    - GPA < thresholds.dismissal -> DISMISSED
    """

    if gpa >= thresholds.good:
        return AcademicStanding.GOOD
    if gpa >= thresholds.probation:
        return AcademicStanding.PROBATION
    if gpa >= thresholds.dismissal:
        return AcademicStanding.SUSPENDED
    return AcademicStanding.DISMISSED

