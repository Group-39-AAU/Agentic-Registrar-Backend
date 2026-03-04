"""
Undergraduate Admission module — Service layer.

Services own all business logic:
    - Workflow state transition enforcement.
    - Trilogy of Persistence (status + history + audit log in one transaction).
    - Human-in-the-loop enforcement for final decisions.
    - Transaction boundary control (commit once).
"""

import uuid
from typing import Optional, Sequence

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger, write_audit_log
from app.modules.undergraduate.exceptions import (
    DuplicateApplicationError,
    EntityNotFoundError,
    InvalidStateTransitionError,
    MissingPrerequisiteError,
    UnauthorizedDecisionError,
)
from app.modules.undergraduate.models import (
    ApplicationDocument,
    ApplicationStatusHistory,
    RegistrarDecision,
    UndergraduateApplication,
)
from app.modules.undergraduate.repository import (
    ApplicationRepository,
    AuditLogRepository,
    DecisionRepository,
    DocumentRepository,
    StatusHistoryRepository,
)
from app.modules.undergraduate.schemas import (
    ApplicationCreate,
    ApplicationStatusUpdate,
    DecisionCreate,
    DocumentCreate,
    DocumentVerify,
)
from app.shared.audit.models import SystemAuditLog
from app.shared.enums import (
    ApplicationStatus,
    DecisionType,
    UserRole,
    VerificationStatus,
)

logger = get_logger("undergraduate.service")

# ── State Machine ────────────────────────────────────────────
# Defines legal state transitions per the registrar lifecycle.

ALLOWED_TRANSITIONS: dict[ApplicationStatus, set[ApplicationStatus]] = {
    ApplicationStatus.DRAFT: {ApplicationStatus.SUBMITTED},
    ApplicationStatus.SUBMITTED: {ApplicationStatus.UNDER_VERIFICATION},
    ApplicationStatus.UNDER_VERIFICATION: {ApplicationStatus.AI_PRE_SCREENING},
    ApplicationStatus.AI_PRE_SCREENING: {ApplicationStatus.PENDING_REVIEW},
    ApplicationStatus.PENDING_REVIEW: {ApplicationStatus.DECIDED},
    ApplicationStatus.DECIDED: set(),  # Terminal state
}


# ══════════════════════════════════════════════════════════════
#  Application Service
# ══════════════════════════════════════════════════════════════


class ApplicationService:
    """
    Orchestrates the undergraduate application lifecycle.
    Owns transaction boundaries (calls commit once per operation).
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._app_repo = ApplicationRepository(db)
        self._history_repo = StatusHistoryRepository(db)
        self._audit_repo = AuditLogRepository(db)
        self._doc_repo = DocumentRepository(db)

    # ── Submit Application ────────────────────────────────────

    async def submit_application(
        self,
        data: ApplicationCreate,
        actor_id: uuid.UUID,
    ) -> UndergraduateApplication:
        """
        Create a new application in DRAFT, then immediately transition to SUBMITTED.
        Raises DuplicateApplicationError if same program/term already exists.
        """
        application = UndergraduateApplication(
            applicant_id=actor_id,
            program_id=data.program_id,
            admission_term=data.admission_term,
            current_status=ApplicationStatus.DRAFT,
            extra_data=data.extra_data,
        )
        self._app_repo.add(application)

        try:
            await self._db.flush()  # get the ID, check unique constraint
        except IntegrityError as e:
            await self._db.rollback()
            # Only raise DuplicateApplicationError for the specific unique constraint.
            # FK violations (missing program/user) should surface as real errors.
            if "uq_one_app_per_program_per_term" in str(e.orig):
                raise DuplicateApplicationError()
            raise

        # Auto-transition DRAFT → SUBMITTED
        await self._transition_status(
            application=application,
            new_status=ApplicationStatus.SUBMITTED,
            actor_id=actor_id,
            actor_role=UserRole.STUDENT,
            trigger_reason="Application submitted by student",
        )

        await self._db.commit()
        await self._db.refresh(application)
        return application

    # ── Change Status ─────────────────────────────────────────

    async def change_status(
        self,
        application_id: uuid.UUID,
        data: ApplicationStatusUpdate,
        actor_id: uuid.UUID,
        actor_role: UserRole,
    ) -> UndergraduateApplication:
        """
        Transition an application to a new status.
        Enforces the state machine and the Trilogy of Persistence.
        """
        application = await self._app_repo.get_by_id(application_id)
        if application is None:
            raise EntityNotFoundError("UndergraduateApplication", str(application_id))

        await self._transition_status(
            application=application,
            new_status=data.new_status,
            actor_id=actor_id,
            actor_role=actor_role,
            trigger_reason=data.trigger_reason,
        )

        await self._db.commit()
        await self._db.refresh(application)
        return application

    # ── Read Operations ───────────────────────────────────────

    async def get_application(
        self, application_id: uuid.UUID
    ) -> UndergraduateApplication:
        application = await self._app_repo.get_by_id(application_id)
        if application is None:
            raise EntityNotFoundError("UndergraduateApplication", str(application_id))
        return application

    async def list_applications(
        self, *, limit: int = 50, offset: int = 0
    ) -> tuple[Sequence[UndergraduateApplication], int]:
        return await self._app_repo.get_all(limit=limit, offset=offset)

    async def list_my_applications(
        self, applicant_id: uuid.UUID
    ) -> Sequence[UndergraduateApplication]:
        return await self._app_repo.get_applications_for_applicant(applicant_id)

    async def get_review_queue(
        self, *, limit: int = 50, offset: int = 0
    ) -> Sequence[UndergraduateApplication]:
        return await self._app_repo.get_pending_review_queue(limit=limit, offset=offset)

    # ── Document Operations ───────────────────────────────────

    async def add_document(
        self,
        data: DocumentCreate,
        actor_id: uuid.UUID,
    ) -> ApplicationDocument:
        """Attach a document to an application."""
        application = await self._app_repo.get_by_id(data.application_id)
        if application is None:
            raise EntityNotFoundError("UndergraduateApplication", str(data.application_id))

        document = ApplicationDocument(
            application_id=data.application_id,
            document_type=data.document_type,
            storage_path=data.storage_path,
        )
        self._doc_repo.add(document)
        await self._db.commit()
        await self._db.refresh(document)
        return document

    async def verify_document(
        self,
        document_id: uuid.UUID,
        data: DocumentVerify,
        actor_id: uuid.UUID,
        actor_role: UserRole,
    ) -> ApplicationDocument:
        """Officer verifies or rejects a document."""
        document = await self._doc_repo.get_by_id(document_id)
        if document is None:
            raise EntityNotFoundError("ApplicationDocument", str(document_id))

        document.verification_status = data.verification_status
        document.verified_by_id = actor_id

        # Audit the verification action
        audit_entry = SystemAuditLog(
            actor_id=actor_id,
            actor_role=actor_role.value,
            action="document.verified",
            resource_type="ApplicationDocument",
            resource_id=document.id,
            decision=data.verification_status.value,
        )
        self._audit_repo.add(audit_entry)

        write_audit_log(
            action="document.verified",
            actor_id=actor_id,
            actor_role=actor_role.value,
            resource_type="ApplicationDocument",
            resource_id=document.id,
            decision=data.verification_status.value,
        )

        await self._db.commit()
        await self._db.refresh(document)
        return document

    # ── Status History ────────────────────────────────────────

    async def get_status_history(
        self, application_id: uuid.UUID
    ) -> Sequence[ApplicationStatusHistory]:
        return await self._history_repo.get_history_for_application(application_id)

    # ── Trilogy of Persistence (private) ──────────────────────

    async def _transition_status(
        self,
        application: UndergraduateApplication,
        new_status: ApplicationStatus,
        actor_id: uuid.UUID,
        actor_role: UserRole,
        trigger_reason: Optional[str] = None,
    ) -> None:
        """
        Atomically:
            1. Update application.current_status
            2. Insert ApplicationStatusHistory row
            3. Insert SystemAuditLog row
        All in the same flush (committed by the caller).
        """
        old_status = application.current_status

        # ── Validate transition ──
        allowed = ALLOWED_TRANSITIONS.get(old_status, set())
        if new_status not in allowed:
            raise InvalidStateTransitionError(old_status.value, new_status.value)

        # ── 1. Update application ──
        application.current_status = new_status

        # ── 2. Insert status history ──
        history_entry = ApplicationStatusHistory(
            application_id=application.id,
            previous_status=old_status,
            new_status=new_status,
            changed_by_id=actor_id,
            trigger_reason=trigger_reason,
        )
        self._history_repo.add(history_entry)

        # ── 3. Insert audit log ──
        audit_entry = SystemAuditLog(
            actor_id=actor_id,
            actor_role=actor_role.value,
            action=f"application.status_changed.{new_status.value.lower()}",
            resource_type="UndergraduateApplication",
            resource_id=application.id,
            metadata_payload={
                "previous_status": old_status.value,
                "new_status": new_status.value,
                "trigger_reason": trigger_reason,
            },
        )
        self._audit_repo.add(audit_entry)

        # ── Emit structured log to stdout ──
        write_audit_log(
            action=f"application.status_changed.{new_status.value.lower()}",
            actor_id=actor_id,
            actor_role=actor_role.value,
            resource_type="UndergraduateApplication",
            resource_id=application.id,
            metadata={
                "previous_status": old_status.value,
                "new_status": new_status.value,
                "trigger_reason": trigger_reason,
            },
        )

        logger.info(
            "Transition %s → %s for application %s by %s",
            old_status.value, new_status.value, application.id, actor_id,
        )


# ══════════════════════════════════════════════════════════════
#  Registrar Decision Service (Human-in-the-Loop)
# ══════════════════════════════════════════════════════════════


class DecisionService:
    """
    Handles the registrar's final decision on an application.
    Enforces human-in-the-loop: only REGISTRAR_OFFICER or ADMIN may decide.
    """

    DECISION_ROLES = {UserRole.REGISTRAR_OFFICER, UserRole.ADMIN}

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._app_repo = ApplicationRepository(db)
        self._decision_repo = DecisionRepository(db)
        self._history_repo = StatusHistoryRepository(db)
        self._audit_repo = AuditLogRepository(db)

    async def record_decision(
        self,
        application_id: uuid.UUID,
        data: DecisionCreate,
        actor_id: uuid.UUID,
        actor_role: UserRole,
    ) -> RegistrarDecision:
        """
        Record the official human decision for an application.
        Transitions application to DECIDED status.
        """
        # ── 1. Role gate ──
        if actor_role not in self.DECISION_ROLES:
            raise UnauthorizedDecisionError(
                f"Role {actor_role.value} cannot record final decisions"
            )

        # ── 2. Fetch application ──
        application = await self._app_repo.get_by_id(application_id)
        if application is None:
            raise EntityNotFoundError("UndergraduateApplication", str(application_id))

        # ── 3. Validate state ──
        if application.current_status != ApplicationStatus.PENDING_REVIEW:
            raise InvalidStateTransitionError(
                application.current_status.value,
                ApplicationStatus.DECIDED.value,
            )

        # ── 4. Check for existing decision (idempotency) ──
        existing = await self._decision_repo.get_by_application_id(application_id)
        if existing is not None:
            return existing  # idempotent: return existing decision

        # ── 5. Create decision record ──
        decision = RegistrarDecision(
            application_id=application_id,
            reviewer_id=actor_id,
            human_decision=data.human_decision,
            justification_remarks=data.justification_remarks,
            override_reason=data.override_reason,
        )
        self._decision_repo.add(decision)

        # ── 6. Transition to DECIDED (Trilogy of Persistence) ──
        old_status = application.current_status
        application.current_status = ApplicationStatus.DECIDED
        application.final_decision = data.human_decision.value

        history_entry = ApplicationStatusHistory(
            application_id=application_id,
            previous_status=old_status,
            new_status=ApplicationStatus.DECIDED,
            changed_by_id=actor_id,
            trigger_reason=f"Registrar decision: {data.human_decision.value}",
        )
        self._history_repo.add(history_entry)

        audit_entry = SystemAuditLog(
            actor_id=actor_id,
            actor_role=actor_role.value,
            action="application.decision.recorded",
            resource_type="UndergraduateApplication",
            resource_id=application_id,
            decision=data.human_decision.value,
            metadata_payload={
                "justification": data.justification_remarks,
                "override_reason": data.override_reason,
            },
        )
        self._audit_repo.add(audit_entry)

        write_audit_log(
            action="application.decision.recorded",
            actor_id=actor_id,
            actor_role=actor_role.value,
            resource_type="UndergraduateApplication",
            resource_id=application_id,
            decision=data.human_decision.value,
            metadata={
                "justification": data.justification_remarks,
                "override_reason": data.override_reason,
            },
        )

        # ── 7. Single commit ──
        await self._db.commit()
        await self._db.refresh(decision)
        return decision

    async def get_decision(
        self, application_id: uuid.UUID
    ) -> Optional[RegistrarDecision]:
        return await self._decision_repo.get_by_application_id(application_id)
