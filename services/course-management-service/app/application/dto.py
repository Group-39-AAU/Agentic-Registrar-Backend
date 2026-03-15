from __future__ import annotations

from pydantic import BaseModel


class RegistrationCreateRequest(BaseModel):
    student_id: str
    term: str
    courses: list[tuple[str, float]]  # (course_code, credits)


class RegistrationResponse(BaseModel):
    id: str
    student_id: str
    term: str
    status: str
    total_credits: float


class AddDropRequestDto(BaseModel):
    registration_id: str
    action: str  # "add" or "drop"
    course_code: str
    credits: float


class GradeSubmissionRequest(BaseModel):
    section_id: str
    submitted_by: str
    entries: list[tuple[str, str, float, float]]  # (student_id, course_code, grade, credits)


class GradeSubmissionResponse(BaseModel):
    id: str
    section_id: str
    submitted_by: str
    status: str


class StandingComputeRequest(BaseModel):
    student_id: str
    term: str
    grades: list[float]
    credits: list[float]
    term_gpas: list[float] | None = None
    term_credits: list[float] | None = None


class StandingResponse(BaseModel):
    id: str
    student_id: str
    term: str
    gpa: float
    cgpa: float
    standing: str
    authorized: bool

