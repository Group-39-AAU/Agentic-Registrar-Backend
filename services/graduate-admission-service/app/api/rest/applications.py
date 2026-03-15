from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from contracts.responses import ResponseEnvelope

from ...application.dto import (
    ApplicantCreateRequest,
    ApplicantResponse,
    ApplicationCreateRequest,
    ApplicationDocumentCreateRequest,
    ApplicationListResponse,
    ApplicationResponse,
    DepartmentDecisionRequest,
    RegistrarDecisionRequest,
)
from ...application.services import GraduateApplicationService
from ...domain.entities import GraduateApplication
from ...domain.enums import ApplicationStatus
from ...infrastructure.db import get_db_session

router = APIRouter(tags=["applications"])


def get_service(session: Session = Depends(get_db_session)) -> GraduateApplicationService:
    return GraduateApplicationService(session=session)


@router.post("/applicants", response_model=ResponseEnvelope[ApplicantResponse])
def create_applicant(
    req: ApplicantCreateRequest,
    service: GraduateApplicationService = Depends(get_service),
):
    applicant = service.create_applicant(req)
    return ResponseEnvelope[ApplicantResponse](data=applicant)


@router.post("/applications", response_model=ResponseEnvelope[ApplicationResponse])
def create_application(
    req: ApplicationCreateRequest,
    service: GraduateApplicationService = Depends(get_service),
):
    app = service.create_application(req)
    return ResponseEnvelope[ApplicationResponse](data=app)


@router.post(
    "/applications/{application_id}/documents",
    response_model=ResponseEnvelope[ApplicationResponse],
)
def add_document(
    application_id: str,
    req: ApplicationDocumentCreateRequest,
    service: GraduateApplicationService = Depends(get_service),
):
    app = service.attach_document(application_id, req)
    return ResponseEnvelope[ApplicationResponse](data=app)


@router.post(
    "/applications/{application_id}/submit",
    response_model=ResponseEnvelope[ApplicationResponse],
)
def submit_application(
    application_id: str,
    service: GraduateApplicationService = Depends(get_service),
):
    app = service.submit_application(application_id)
    return ResponseEnvelope[ApplicationResponse](data=app)


@router.get(
    "/applications/{application_id}",
    response_model=ResponseEnvelope[ApplicationResponse],
)
def get_application(
    application_id: str,
    service: GraduateApplicationService = Depends(get_service),
):
    app = service.get_application(application_id)
    return ResponseEnvelope[ApplicationResponse](data=app)


@router.get("/applications", response_model=ResponseEnvelope[ApplicationListResponse])
def list_applications(
    service: GraduateApplicationService = Depends(get_service),
):
    apps = service.list_applications()
    return ResponseEnvelope[ApplicationListResponse](data=apps)


@router.post(
    "/applications/{application_id}/department-decision",
    response_model=ResponseEnvelope[ApplicationResponse],
)
def department_decision(
    application_id: str,
    req: DepartmentDecisionRequest,
    session: Session = Depends(get_db_session),
):
    app = session.get(GraduateApplication, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="Application not found")
    if app.status not in {
        ApplicationStatus.ROUTED_TO_DEPARTMENT,
        ApplicationStatus.DEPARTMENT_REVIEW,
    }:
        raise HTTPException(status_code=400, detail="Invalid status for department decision")
    app.status = ApplicationStatus.DEPARTMENT_APPROVED if req.approved else ApplicationStatus.REJECTED
    session.commit()
    return ResponseEnvelope[ApplicationResponse](data=GraduateApplicationService._to_response(app))


@router.post(
    "/applications/{application_id}/registrar-decision",
    response_model=ResponseEnvelope[ApplicationResponse],
)
def registrar_decision(
    application_id: str,
    req: RegistrarDecisionRequest,
    session: Session = Depends(get_db_session),
):
    app = session.get(GraduateApplication, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="Application not found")
    if app.status not in {
        ApplicationStatus.DEPARTMENT_APPROVED,
        ApplicationStatus.REGISTRAR_REVIEW,
    }:
        raise HTTPException(status_code=400, detail="Invalid status for registrar decision")
    app.status = ApplicationStatus.APPROVED if req.approved else ApplicationStatus.REJECTED
    session.commit()
    return ResponseEnvelope[ApplicationResponse](data=GraduateApplicationService._to_response(app))

