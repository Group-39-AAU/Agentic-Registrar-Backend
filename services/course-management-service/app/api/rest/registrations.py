from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from contracts.responses import ResponseEnvelope

from ...application.dto import (
    AddDropRequestDto,
    GradeSubmissionRequest,
    GradeSubmissionResponse,
    RegistrationCreateRequest,
    RegistrationResponse,
    StandingComputeRequest,
    StandingResponse,
)
from ...application.services import GradeService, RecordService, RegistrationService, StandingService
from ...infrastructure.db import get_db_session

router = APIRouter(tags=["course-management"])


def get_registration_service(session: Session = Depends(get_db_session)) -> RegistrationService:
    return RegistrationService(session=session)


def get_grade_service(session: Session = Depends(get_db_session)) -> GradeService:
    return GradeService(session=session)


def get_standing_service(session: Session = Depends(get_db_session)) -> StandingService:
    return StandingService(session=session)


def get_record_service(session: Session = Depends(get_db_session)) -> RecordService:
    return RecordService(session=session)


@router.post("/registrations", response_model=ResponseEnvelope[RegistrationResponse])
def create_registration(
    req: RegistrationCreateRequest,
    service: RegistrationService = Depends(get_registration_service),
):
    reg = service.create_registration(req)
    return ResponseEnvelope[RegistrationResponse](data=reg)


@router.post(
    "/registrations/{registration_id}/validate",
    response_model=ResponseEnvelope[RegistrationResponse],
)
def validate_registration(
    registration_id: str,
    service: RegistrationService = Depends(get_registration_service),
):
    reg = service.validate_registration(registration_id)
    return ResponseEnvelope[RegistrationResponse](data=reg)


@router.post(
    "/registrations/{registration_id}/finalize",
    response_model=ResponseEnvelope[RegistrationResponse],
)
def finalize_registration(
    registration_id: str,
    service: RegistrationService = Depends(get_registration_service),
):
    reg = service.finalize_registration(registration_id)
    return ResponseEnvelope[RegistrationResponse](data=reg)


@router.post("/add-drop", response_model=ResponseEnvelope[RegistrationResponse])
def add_drop(
    req: AddDropRequestDto,
    service: RegistrationService = Depends(get_registration_service),
):
    reg = service.add_drop(req)
    return ResponseEnvelope[RegistrationResponse](data=reg)


@router.post("/grades/submit", response_model=ResponseEnvelope[GradeSubmissionResponse])
def submit_grades(
    req: GradeSubmissionRequest,
    service: GradeService = Depends(get_grade_service),
):
    submission = service.submit_grades(req)
    return ResponseEnvelope[GradeSubmissionResponse](data=submission)


@router.post(
    "/grades/{submission_id}/authorize",
    response_model=ResponseEnvelope[GradeSubmissionResponse],
)
def authorize_grades(
    submission_id: str,
    service: GradeService = Depends(get_grade_service),
):
    submission = service.authorize_submission(submission_id)
    return ResponseEnvelope[GradeSubmissionResponse](data=submission)


@router.post("/standing/compute", response_model=ResponseEnvelope[StandingResponse])
def compute_standing(
    req: StandingComputeRequest,
    service: StandingService = Depends(get_standing_service),
):
    standing = service.compute_standing(req)
    return ResponseEnvelope[StandingResponse](data=standing)


@router.post(
    "/standing/{standing_id}/authorize",
    response_model=ResponseEnvelope[StandingResponse],
)
def authorize_standing(
    standing_id: str,
    service: StandingService = Depends(get_standing_service),
):
    standing = service.authorize_standing(standing_id)
    return ResponseEnvelope[StandingResponse](data=standing)


@router.post("/records/generate")
def generate_record(
    student_id: str,
    service: RecordService = Depends(get_record_service),
):
    record_id = service.generate_record(student_id)
    return {"record_id": record_id}


@router.post("/exceptions/{case_id}/resolve")
def resolve_exception(
    case_id: str,
    service: RecordService = Depends(get_record_service),
):
    service.resolve_exception(case_id)
    return {"status": "resolved"}

