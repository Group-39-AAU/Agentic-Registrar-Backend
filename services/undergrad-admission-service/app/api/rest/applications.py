from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from contracts.responses import ResponseEnvelope

from ...application.dto.models import (
    ApplicantCreateRequest,
    ApplicantResponse,
    ApplicationCreateRequest,
    ApplicationDocumentCreateRequest,
    ApplicationListResponse,
    ApplicationResponse,
    OfficerDecisionRequest,
)
from ...application.services.app_service import UndergradApplicationService
from ...domain.enums import ApplicationStatus
from ...infrastructure.db.session import get_db_session

router = APIRouter(prefix="/applications", tags=["applications"])


def get_service(session: Session = Depends(get_db_session)) -> UndergradApplicationService:
    return UndergradApplicationService(session=session)


@router.post("/applicants", response_model=ResponseEnvelope[ApplicantResponse])
def create_applicant(
    req: ApplicantCreateRequest,
    service: UndergradApplicationService = Depends(get_service),
):
    applicant = service.create_applicant(req)
    return ResponseEnvelope[ApplicantResponse](data=applicant)


@router.post("", response_model=ResponseEnvelope[ApplicationResponse])
def create_application(
    req: ApplicationCreateRequest,
    service: UndergradApplicationService = Depends(get_service),
):
    app = service.create_application(req)
    return ResponseEnvelope[ApplicationResponse](data=app)


@router.post("/{application_id}/documents", response_model=ResponseEnvelope[ApplicationResponse])
def add_document(
    application_id: str,
    req: ApplicationDocumentCreateRequest,
    service: UndergradApplicationService = Depends(get_service),
):
    app = service.attach_document(application_id, req)
    return ResponseEnvelope[ApplicationResponse](data=app)


@router.post("/{application_id}/submit", response_model=ResponseEnvelope[ApplicationResponse])
def submit_application(
    application_id: str,
    service: UndergradApplicationService = Depends(get_service),
):
    app = service.submit_application(application_id)
    return ResponseEnvelope[ApplicationResponse](data=app)


@router.get("/{application_id}", response_model=ResponseEnvelope[ApplicationResponse])
def get_application(
    application_id: str,
    service: UndergradApplicationService = Depends(get_service),
):
    app = service.get_application(application_id)
    return ResponseEnvelope[ApplicationResponse](data=app)


@router.get("", response_model=ResponseEnvelope[ApplicationListResponse])
def list_applications(
    service: UndergradApplicationService = Depends(get_service),
):
    apps = service.list_applications()
    return ResponseEnvelope[ApplicationListResponse](data=apps)


@router.post(
    "/{application_id}/officer-decision",
    response_model=ResponseEnvelope[ApplicationResponse],
)
def officer_decision(
    application_id: str,
    req: OfficerDecisionRequest,
    service: UndergradApplicationService = Depends(get_service),
):
    # For now, just update status field in a lightweight way.
    current = service.get_application(application_id)
    updated = ApplicationResponse(
        id=current.id,
        applicant_id=current.applicant_id,
        program_code=current.program_code,
        intake_year=current.intake_year,
        status=req.decision,
        cumulative_score=current.cumulative_score,
        ranking_position=current.ranking_position,
    )
    return ResponseEnvelope[ApplicationResponse](data=updated)

