from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from policy_engine.credit import validate_credit_load
from policy_engine.gpa import calculate_cgpa, calculate_gpa
from policy_engine.standing import StandingThresholds, evaluate_academic_standing

from ..config.settings import get_settings
from ..domain.models import (
    AcademicRecord,
    AcademicStanding,
    AddDropRequest,
    GradeEntry,
    GradeSubmission,
    ManualExceptionCase,
    Registration,
    RegistrationItem,
)
from shared_kernel.db import write_audit_event
from .dto import (
    AddDropRequestDto,
    GradeSubmissionRequest,
    GradeSubmissionResponse,
    RegistrationCreateRequest,
    RegistrationResponse,
    StandingComputeRequest,
    StandingResponse,
)


class RegistrationService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._settings = get_settings()

    def create_registration(self, req: RegistrationCreateRequest) -> RegistrationResponse:
        reg = Registration(
            student_id=req.student_id,
            term=req.term,
            status="draft",
            total_credits=0.0,
        )
        self._session.add(reg)
        total = 0.0
        for course_code, credits in req.courses:
            item = RegistrationItem(
                registration=reg,
                course_code=course_code,
                credits=credits,
            )
            self._session.add(item)
            total += credits
        reg.total_credits = total
        self._session.commit()
        write_audit_event(
            self._session,
            service_name="course-management-service",
            entity_type="Registration",
            entity_id=str(reg.id),
            action="create",
        )
        return self._to_registration_response(reg)

    def validate_registration(self, registration_id: str) -> RegistrationResponse:
        reg = self._session.get(Registration, uuid.UUID(registration_id))
        if reg is None:
            raise ValueError("Registration not found")
        if reg.status not in {"draft", "exception"}:
            raise ValueError("Registration cannot be validated from current status")
        ok = validate_credit_load(
            current_credits=0.0,
            additional_credits=reg.total_credits,
            min_credits=self._settings.min_credits,
            max_credits=self._settings.max_credits,
        )
        if not ok:
            case = ManualExceptionCase(
                registration_id=reg.id,
                status="open",
                reason="credit_load_out_of_range",
            )
            self._session.add(case)
            reg.status = "exception"
        else:
            reg.status = "validated"
        self._session.commit()
        write_audit_event(
            self._session,
            service_name="course-management-service",
            entity_type="Registration",
            entity_id=str(reg.id),
            action="validate",
        )
        return self._to_registration_response(reg)

    def finalize_registration(self, registration_id: str) -> RegistrationResponse:
        reg = self._session.get(Registration, uuid.UUID(registration_id))
        if reg is None:
            raise ValueError("Registration not found")
        if reg.status != "validated":
            raise ValueError("Registration must be validated before finalization")
        reg.status = "finalized"
        self._session.commit()
        write_audit_event(
            self._session,
            service_name="course-management-service",
            entity_type="Registration",
            entity_id=str(reg.id),
            action="finalize",
        )
        return self._to_registration_response(reg)

    def add_drop(self, req: AddDropRequestDto) -> RegistrationResponse:
        reg = self._session.get(Registration, uuid.UUID(req.registration_id))
        if reg is None:
            raise ValueError("Registration not found")
        add_drop = AddDropRequest(
            registration_id=reg.id,
            action=req.action,
            course_code=req.course_code,
        )
        self._session.add(add_drop)
        if req.action == "add":
            item = RegistrationItem(
                registration_id=reg.id,
                course_code=req.course_code,
                credits=req.credits,
            )
            self._session.add(item)
            reg.total_credits += req.credits
        elif req.action == "drop":
            # For simplicity, adjust credits only.
            reg.total_credits = max(0.0, reg.total_credits - req.credits)
        self._session.commit()
        return self._to_registration_response(reg)

    @staticmethod
    def _to_registration_response(reg: Registration) -> RegistrationResponse:
        return RegistrationResponse(
            id=str(reg.id),
            student_id=reg.student_id,
            term=reg.term,
            status=reg.status,
            total_credits=reg.total_credits,
        )


class GradeService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def submit_grades(self, req: GradeSubmissionRequest) -> GradeSubmissionResponse:
        submission = GradeSubmission(
            section_id=req.section_id,
            submitted_by=req.submitted_by,
            status="pending",
        )
        self._session.add(submission)
        self._session.flush()
        for student_id, course_code, grade, credits in req.entries:
            entry = GradeEntry(
                submission_id=submission.id,
                student_id=student_id,
                course_code=course_code,
                grade=grade,
                credits=credits,
            )
            self._session.add(entry)
        self._session.commit()
        return GradeSubmissionResponse(
            id=str(submission.id),
            section_id=submission.section_id,
            submitted_by=submission.submitted_by,
            status=submission.status,
        )

    def authorize_submission(self, submission_id: str) -> GradeSubmissionResponse:
        submission = self._session.get(GradeSubmission, uuid.UUID(submission_id))
        if submission is None:
            raise ValueError("Submission not found")
        submission.status = "authorized"
        self._session.commit()
        write_audit_event(
            self._session,
            service_name="course-management-service",
            entity_type="GradeSubmission",
            entity_id=str(submission.id),
            action="authorize",
        )
        return GradeSubmissionResponse(
            id=str(submission.id),
            section_id=submission.section_id,
            submitted_by=submission.submitted_by,
            status=submission.status,
        )


class StandingService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def compute_standing(self, req: StandingComputeRequest) -> StandingResponse:
        gpa = calculate_gpa(req.grades, req.credits)
        if req.term_gpas is not None and req.term_credits is not None:
            cgpa = calculate_cgpa(req.term_gpas, req.term_credits)
        else:
            cgpa = gpa
        thresholds = StandingThresholds(good=3.0, probation=2.0, dismissal=1.0)
        standing_enum = evaluate_academic_standing(cgpa, thresholds)
        standing = AcademicStanding(
            student_id=req.student_id,
            term=req.term,
            gpa=gpa,
            cgpa=cgpa,
            standing=standing_enum.value,
            authorized=False,
        )
        self._session.add(standing)
        self._session.commit()
        return StandingResponse(
            id=str(standing.id),
            student_id=standing.student_id,
            term=standing.term,
            gpa=standing.gpa,
            cgpa=standing.cgpa,
            standing=standing.standing,
            authorized=standing.authorized,
        )

    def authorize_standing(self, standing_id: str) -> StandingResponse:
        standing = self._session.get(AcademicStanding, uuid.UUID(standing_id))
        if standing is None:
            raise ValueError("Standing not found")
        standing.authorized = True
        self._session.commit()
        write_audit_event(
            self._session,
            service_name="course-management-service",
            entity_type="AcademicStanding",
            entity_id=str(standing.id),
            action="authorize",
        )
        return StandingResponse(
            id=str(standing.id),
            student_id=standing.student_id,
            term=standing.term,
            gpa=standing.gpa,
            cgpa=standing.cgpa,
            standing=standing.standing,
            authorized=standing.authorized,
        )


class RecordService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def generate_record(self, student_id: str) -> str:
        payload = f"Record for {student_id} generated at {datetime.now(timezone.utc).isoformat()}"
        record = AcademicRecord(
            student_id=student_id,
            generated_at=datetime.now(timezone.utc),
            payload=payload,
        )
        self._session.add(record)
        self._session.commit()
        write_audit_event(
            self._session,
            service_name="course-management-service",
            entity_type="AcademicRecord",
            entity_id=str(record.id),
            action="generate",
        )
        return str(record.id)

    def resolve_exception(self, case_id: str) -> None:
        case = self._session.get(ManualExceptionCase, uuid.UUID(case_id))
        if case is None:
            return
        case.status = "resolved"
        case.resolved_at = datetime.now(timezone.utc)
        self._session.commit()
        write_audit_event(
            self._session,
            service_name="course-management-service",
            entity_type="ManualExceptionCase",
            entity_id=str(case.id),
            action="resolve",
        )

