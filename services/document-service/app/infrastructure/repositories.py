from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from shared_kernel.exceptions import NotFoundError

from ..domain.models import Document


class DocumentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, document_id: uuid.UUID) -> Document:
        stmt = select(Document).where(Document.id == document_id)
        doc = self._session.execute(stmt).scalars().first()
        if doc is None:
            raise NotFoundError(f"Document {document_id} not found")
        return doc

    def add(self, document: Document) -> None:
        self._session.add(document)

