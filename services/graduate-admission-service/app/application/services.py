from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from shared_kernel.utils import generate_uuid

from ..domain.entities import (
    GraduateApplicantProfile,
    GraduateApplication,
    GraduateDocumentReference,
)
from ..domain.enums import ApplicationStatus
from .dto import (
    ApplicantCreateRequest,
    ApplicantResponse,
    ApplicationCreateRequest,
    ApplicationDocumentCreateRequest,
    ApplicationListResponse,
    ApplicationResponse,
)


class GraduateApplicationService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create_applicant(self, req: ApplicantCreateRequest) -> ApplicantResponse:
        obj = GraduateApplicantProfile(
            id=generate_uuid(),
            full_name=req.full_name,
            email=req.email,
            phone_number=req.phone_number,
            national_id=req.national_id,
        )
        self._session.add(obj)
        self._session.commit()
        return ApplicantResponse(
            id=str(obj.id),
            full_name=obj.full_name,
            email=obj.email,
            phone_number=obj.phone_number,
            national_id=obj.national_id,
        )

    def create_application(self, req: ApplicationCreateRequest) -> ApplicationResponse:
        app = GraduateApplication(
            id=generate_uuid(),
            applicant_id=uuid.UUID(req.applicant_id),
            program_code=req.program_code,
            intake_year=req.intake_year,
            status=ApplicationStatus.DRAFT,
        )
        self._session.add(app)
        self._session.commit()
        return self._to_response(app)

    def attach_document(
        self,
        application_id: str,
        req: ApplicationDocumentCreateRequest,
    ) -> ApplicationResponse:
        app = self._session.get(GraduateApplication, uuid.UUID(application_id))
        if app is None:
            raise ValueError("Application not found")
        ref = GraduateDocumentReference(
            id=generate_uuid(),
            application_id=app.id,
            document_id=uuid.UUID(req.document_id),
            document_type=req.document_type,
        )
        self._session.add(ref)
        self._session.commit()
        self._session.refresh(app)
        return self._to_response(app)

    def submit_application(self, application_id: str) -> ApplicationResponse:
        app = self._session.get(GraduateApplication, uuid.UUID(application_id))
        if app is None:
            raise ValueError("Application not found")
        app.status = ApplicationStatus.SUBMITTED
        self._session.commit()
        return self._to_response(app)

    def get_application(self, application_id: str) -> ApplicationResponse:
        app = self._session.get(GraduateApplication, uuid.UUID(application_id))
        if app is None:
            raise ValueError("Application not found")
        return self._to_response(app)

    def list_applications(self) -> ApplicationListResponse:
        apps = self._session.query(GraduateApplication).all()
        return ApplicationListResponse(items=[self._to_response(a) for a in apps])

    @staticmethod
    def _to_response(app: GraduateApplication) -> ApplicationResponse:
        return ApplicationResponse(
            id=str(app.id),
            applicant_id=str(app.applicant_id),
            program_code=app.program_code,
            intake_year=app.intake_year,
            status=ApplicationStatus(app.status),
            gat_score=app.gat_score,
            transcript_score=app.transcript_score,
            cumulative_score=app.cumulative_score,
            ranking_position=app.ranking_position,
        )

