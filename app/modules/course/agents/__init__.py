"""
Course Management agents.

This package will hold the ten autonomous agents listed in SDS §3.1.3
Course Management Class Diagram (Figure 5):

    Track A — Curriculum Compliance, Academic Scheduling,
              Enrollment Adjustment, Academic Advisory
    Track B — Assessment Validation, Grade Monitoring,
              Grade Authorization Facilitator
    Track C — Academic Standing, Academic Records,
              Exception Resolution

Every concrete agent extends :class:`CourseBaseAgent`, which itself
extends :class:`app.ai.base.BaseAgent`.
"""

from app.modules.course.agents.academic_scheduling_agent import (
    AcademicSchedulingAgent,
    AllocationResult,
    ScheduleArtefact,
)
from app.modules.course.agents.course_base_agent import CourseBaseAgent
from app.modules.course.agents.curriculum_compliance_agent import (
    ComplianceCheckResult,
    CurriculumComplianceAgent,
    MAX_CREDIT_LOAD_ECTS,
)

__all__ = [
    "AcademicSchedulingAgent",
    "AllocationResult",
    "ComplianceCheckResult",
    "CourseBaseAgent",
    "CurriculumComplianceAgent",
    "MAX_CREDIT_LOAD_ECTS",
    "ScheduleArtefact",
]
