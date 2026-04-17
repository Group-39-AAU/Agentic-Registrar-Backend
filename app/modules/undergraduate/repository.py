"""
Undergraduate Admission module — Repositories.

Per the architecture blueprint:
    - One repository per aggregate root.
    - Repositories accept AsyncSession via DI, never call commit/rollback.
    - Soft-delete filtering is automatic on all reads.
    - Immutable ledgers only support add() and read operations.
    - Intent-based query methods that read like registrar workflows.
    - Pagination via limit/offset with deterministic ordering.
"""

import uuid
from typing import Optional, Sequence

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.undergraduate.models import (
    ApplicationDocument,
    ApplicationStatusHistory,
    RegistrarDecision,
    UndergraduateApplication,
)
from app.shared.audit.models import SystemAuditLog
from app.shared.enums import ApplicationStatus


# ══════════════════════════════════════════════════════════════
#  Application Repository (Core Aggregate)
# ══════════════════════════════════════════════════════════════


class ApplicationRepository:
    """Data access for UndergraduateApplication (soft-delete aware)."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ── Reads (auto-filter is_deleted=False) ──

    async def get_by_id(
        self, app_id: uuid.UUID, *, include_deleted: bool = False
    ) -> Optional[UndergraduateApplication]:
        stmt = select(UndergraduateApplication).where(
            UndergraduateApplication.id == app_id
        )
        if not include_deleted:
            stmt = stmt.where(UndergraduateApplication.is_deleted == False)  # noqa: E712
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_applications_for_applicant(
        self, applicant_id: uuid.UUID
    ) -> Sequence[UndergraduateApplication]:
        stmt = (
            select(UndergraduateApplication)
            .where(
                UndergraduateApplication.applicant_id == applicant_id,
                UndergraduateApplication.is_deleted == False,  # noqa: E712
            )
            .order_by(UndergraduateApplication.created_at.desc())
        )
        result = await self._db.execute(stmt)
        return result.scalars().all()

    async def exists_for_applicant_and_term(
        self, applicant_id: uuid.UUID, admission_term_id: uuid.UUID
    ) -> bool:
        stmt = (
            select(UndergraduateApplication.id)
            .where(
                UndergraduateApplication.applicant_id == applicant_id,
                UndergraduateApplication.admission_term_id == admission_term_id,
                UndergraduateApplication.is_deleted == False,  # noqa: E712
            )
            .limit(1)
        )
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def get_pending_review_queue(
        self, *, limit: int = 50, offset: int = 0
    ) -> Sequence[UndergraduateApplication]:
        stmt = (
            select(UndergraduateApplication)
            .where(
                UndergraduateApplication.current_status == ApplicationStatus.PENDING_REVIEW,
                UndergraduateApplication.is_deleted == False,  # noqa: E712
            )
            .order_by(UndergraduateApplication.created_at.asc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._db.execute(stmt)
        return result.scalars().all()

    async def get_all(
        self, *, limit: int = 50, offset: int = 0
    ) -> tuple[Sequence[UndergraduateApplication], int]:
        """Returns (items, total_count) for paginated listing."""
        count_stmt = (
            select(func.count())
            .select_from(UndergraduateApplication)
            .where(UndergraduateApplication.is_deleted == False)  # noqa: E712
        )
        total = (await self._db.execute(count_stmt)).scalar_one()

        items_stmt = (
            select(UndergraduateApplication)
            .where(UndergraduateApplication.is_deleted == False)  # noqa: E712
            .order_by(UndergraduateApplication.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        items = (await self._db.execute(items_stmt)).scalars().all()
        return items, total

    # ── Writes ──

    def add(self, application: UndergraduateApplication) -> None:
        self._db.add(application)

    async def soft_delete(self, application: UndergraduateApplication) -> None:
        application.is_deleted = True


# ══════════════════════════════════════════════════════════════
#  Document Repository (Supporting Entity)
# ══════════════════════════════════════════════════════════════


class DocumentRepository:
    """Data access for ApplicationDocument (soft-delete aware)."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_by_id(
        self, doc_id: uuid.UUID
    ) -> Optional[ApplicationDocument]:
        stmt = select(ApplicationDocument).where(
            ApplicationDocument.id == doc_id,
            ApplicationDocument.is_deleted == False,  # noqa: E712
        )
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_documents_for_application(
        self, application_id: uuid.UUID
    ) -> Sequence[ApplicationDocument]:
        stmt = (
            select(ApplicationDocument)
            .where(
                ApplicationDocument.application_id == application_id,
                ApplicationDocument.is_deleted == False,  # noqa: E712
            )
            .order_by(ApplicationDocument.created_at.asc())
        )
        result = await self._db.execute(stmt)
        return result.scalars().all()

    def add(self, document: ApplicationDocument) -> None:
        self._db.add(document)


# ══════════════════════════════════════════════════════════════
#  Status History Repository (Immutable Ledger)
# ══════════════════════════════════════════════════════════════


class StatusHistoryRepository:
    """
    Data access for ApplicationStatusHistory.
    IMMUTABLE: supports only add() and read. No update, no delete.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_history_for_application(
        self, application_id: uuid.UUID
    ) -> Sequence[ApplicationStatusHistory]:
        stmt = (
            select(ApplicationStatusHistory)
            .where(ApplicationStatusHistory.application_id == application_id)
            .order_by(ApplicationStatusHistory.created_at.asc())
        )
        result = await self._db.execute(stmt)
        return result.scalars().all()

    async def get_latest_status(
        self, application_id: uuid.UUID
    ) -> Optional[ApplicationStatusHistory]:
        stmt = (
            select(ApplicationStatusHistory)
            .where(ApplicationStatusHistory.application_id == application_id)
            .order_by(ApplicationStatusHistory.created_at.desc())
            .limit(1)
        )
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    def add(self, entry: ApplicationStatusHistory) -> None:
        self._db.add(entry)


# ══════════════════════════════════════════════════════════════
#  Registrar Decision Repository (Immutable)
# ══════════════════════════════════════════════════════════════


class DecisionRepository:
    """
    Data access for RegistrarDecision.
    IMMUTABLE: supports only add() and read. No update, no delete.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_by_application_id(
        self, application_id: uuid.UUID
    ) -> Optional[RegistrarDecision]:
        stmt = select(RegistrarDecision).where(
            RegistrarDecision.application_id == application_id
        )
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    def add(self, decision: RegistrarDecision) -> None:
        self._db.add(decision)


# ══════════════════════════════════════════════════════════════
#  Audit Log Repository (Immutable Ledger)
# ══════════════════════════════════════════════════════════════


class AuditLogRepository:
    """
    Data access for SystemAuditLog.
    IMMUTABLE: supports only add() and read. No update, no delete.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_logs_for_resource(
        self, resource_type: str, resource_id: uuid.UUID
    ) -> Sequence[SystemAuditLog]:
        stmt = (
            select(SystemAuditLog)
            .where(
                SystemAuditLog.resource_type == resource_type,
                SystemAuditLog.resource_id == resource_id,
            )
            .order_by(SystemAuditLog.created_at.asc())
        )
        result = await self._db.execute(stmt)
        return result.scalars().all()

    def add(self, log_entry: SystemAuditLog) -> None:
        self._db.add(log_entry)
