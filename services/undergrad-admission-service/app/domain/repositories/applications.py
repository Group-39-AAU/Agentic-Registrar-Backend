from __future__ import annotations

import uuid
from typing import Iterable, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from shared_kernel.exceptions import NotFoundError

from ..entities.models import (
    ApplicantProfile,
    ApplicationDocumentReference,
    UndergraduateApplication,
)
from ..enums import ApplicationStatus


class ApplicantRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, profile: ApplicantProfile) -> None:
        self._session.add(profile)

    def get(self, applicant_id: uuid.UUID) -> ApplicantProfile:
        stmt = select(ApplicantProfile).where(ApplicantProfile.id == applicant_id)
        obj = self._session.execute(stmt).scalars().first()
        if obj is None:
            raise NotFoundError(f"Applicant {applicant_id} not found")
        return obj


class ApplicationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, app: UndergraduateApplication) -> None:
        self._session.add(app)

    def get(self, application_id: uuid.UUID) -> UndergraduateApplication:
        stmt = select(UndergraduateApplication).where(UndergraduateApplication.id == application_id)
        obj = self._session.execute(stmt).scalars().first()
        if obj is None:
            raise NotFoundError(f"Application {application_id} not found")
        return obj

    def list_all(self) -> list[UndergraduateApplication]:
        stmt = select(UndergraduateApplication)
        return list(self._session.execute(stmt).scalars().all())


class ApplicationDocumentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, ref: ApplicationDocumentReference) -> None:
        self._session.add(ref)

