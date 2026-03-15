from __future__ import annotations

import uuid
from typing import List

from sqlalchemy.orm import Session

from shared_kernel.db import write_audit_event
from shared_kernel.utils import generate_uuid

from ...domain.entities.models import (
    ApplicantProfile,
    ApplicationDocumentReference,
    UndergraduateApplication,
)
from ...domain.enums import ApplicationStatus
from ...domain.repositories.applications import (
    ApplicantRepository,
    ApplicationDocumentRepository,
    ApplicationRepository,
)
from ..dto.models import (
    ApplicantCreateRequest,
    ApplicantResponse,
    ApplicationCreateRequest,
    ApplicationDocumentCreateRequest,
    ApplicationListResponse,
    ApplicationResponse,
)


class UndergradApplicationService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._applicants = ApplicantRepository(session)
        self._applications = ApplicationRepository(session)
        self._documents = ApplicationDocumentRepository(session)

    def create_applicant(self, req: ApplicantCreateRequest) -> ApplicantResponse:
        profile = ApplicantProfile(
            id=generate_uuid(),
            full_name=req.full_name,
            email=req.email,
            phone_number=req.phone_number,
            national_id=req.national_id,
        )
        self._applicants.add(profile)
        self._session.commit()
        write_audit_event(
            self._session,
            service_name="undergrad-admission-service",
            entity_type="ApplicantProfile",
            entity_id=str(profile.id),
            action="create",
        )
        return ApplicantResponse(
            id=str(profile.id),
            full_name=profile.full_name,
            email=profile.email,
            phone_number=profile.phone_number,
            national_id=profile.national_id,
        )

    def create_application(self, req: ApplicationCreateRequest) -> ApplicationResponse:
        app = UndergraduateApplication(
            id=generate_uuid(),
            applicant_id=uuid.UUID(req.applicant_id),
            program_code=req.program_code,
            intake_year=req.intake_year,
            status=ApplicationStatus.DRAFT,
        )
        self._applications.add(app)
        self._session.commit()
        write_audit_event(
            self._session,
            service_name="undergrad-admission-service",
            entity_type="UndergraduateApplication",
            entity_id=str(app.id),
            action="create",
        )
        return self._to_response(app)

    def attach_document(
        self,
        application_id: str,
        req: ApplicationDocumentCreateRequest,
    ) -> ApplicationResponse:
        app = self._applications.get(uuid.UUID(application_id))
        ref = ApplicationDocumentReference(
            id=generate_uuid(),
            application_id=app.id,
            document_id=uuid.UUID(req.document_id),
            document_type=req.document_type,
        )
        self._documents.add(ref)
        self._session.commit()
        self._session.refresh(app)
        return self._to_response(app)

    def get_application(self, application_id: str) -> ApplicationResponse:
        app = self._applications.get(uuid.UUID(application_id))
        return self._to_response(app)

    def list_applications(self) -> ApplicationListResponse:
        apps = self._applications.list_all()
        items: List[ApplicationResponse] = [self._to_response(a) for a in apps]
        return ApplicationListResponse(items=items)

    def submit_application(self, application_id: str) -> ApplicationResponse:
        app = self._applications.get(uuid.UUID(application_id))
        if app.status not in {ApplicationStatus.DRAFT, ApplicationStatus.VALIDATION_PENDING}:
            raise ValueError("Application cannot be submitted from current status")
        app.status = ApplicationStatus.SUBMITTED
        self._session.commit()
        write_audit_event(
            self._session,
            service_name="undergrad-admission-service",
            entity_type="UndergraduateApplication",
            entity_id=str(app.id),
            action="submit",
        )
        return self._to_response(app)

    @staticmethod
    def _to_response(app: UndergraduateApplication) -> ApplicationResponse:
        return ApplicationResponse(
            id=str(app.id),
            applicant_id=str(app.applicant_id),
            program_code=app.program_code,
            intake_year=app.intake_year,
            status=ApplicationStatus(app.status),
            cumulative_score=app.cumulative_score,
            ranking_position=app.ranking_position,
        )

