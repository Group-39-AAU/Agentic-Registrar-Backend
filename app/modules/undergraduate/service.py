"""
Undergraduate Admission module — Service layer.

Services own all business logic:
    - Workflow state transition enforcement.
    - Trilogy of Persistence (status + history + audit log in one transaction).
    - Human-in-the-loop enforcement for final decisions.
    - Simulated payment handling.
    - Transaction boundary control (commit once).
"""

import uuid
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger, write_audit_log
from app.modules.programs.models import AcademicProgram
from app.modules.undergraduate.exceptions import (
    DuplicateApplicationError,
    EntityNotFoundError,
    InvalidStateTransitionError,
    MissingPrerequisiteError,
    UnauthorizedDecisionError,
)
from app.modules.undergraduate.models import (
    ApplicationDocument,
    UndergraduateAdmissionTerm,
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
    AdmissionTermCreate,
    AdmissionTermResponse,
    ApplicationResponse,
    ApplicationStatusUpdate,
    DecisionCreate,
    DocumentCreate,
    DocumentVerify,
    ProgramChoiceSummary,
)
from app.modules.testing_center.models import UATRecord
from app.shared.audit.models import SystemAuditLog
from app.shared.enums import (
    ApplicationStatus,
    DecisionType,
    PaymentStatus,
    SponsorshipType,
    UserRole,
    VerificationStatus,
)

logger = get_logger("undergraduate.service")

# ── State Machine ────────────────────────────────────────────
# Defines legal state transitions per the registrar lifecycle.

ALLOWED_TRANSITIONS: dict[ApplicationStatus, set[ApplicationStatus]] = {
    ApplicationStatus.DRAFT: {ApplicationStatus.SUBMITTED},
    ApplicationStatus.SUBMITTED: {ApplicationStatus.PAYMENT_PENDING},
    ApplicationStatus.PAYMENT_PENDING: {ApplicationStatus.PAYMENT_VERIFIED},
    ApplicationStatus.PAYMENT_VERIFIED: {ApplicationStatus.UNDER_VERIFICATION},
    ApplicationStatus.UNDER_VERIFICATION: {ApplicationStatus.AI_PRE_SCREENING, ApplicationStatus.FLAGGED_FOR_REVIEW},
    ApplicationStatus.AI_PRE_SCREENING: {ApplicationStatus.UAT_PENDING, ApplicationStatus.FLAGGED_FOR_REVIEW},
    ApplicationStatus.UAT_PENDING: {ApplicationStatus.UAT_COMPLETED},
    ApplicationStatus.UAT_COMPLETED: {ApplicationStatus.PENDING_REVIEW},
    ApplicationStatus.PENDING_REVIEW: {ApplicationStatus.DECIDED},
    ApplicationStatus.DECIDED: {ApplicationStatus.ENROLLED},
    ApplicationStatus.ENROLLED: set(),  # Terminal state
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
        Create a new application in DRAFT, then transition to SUBMITTED.
        For government-sponsored: auto-set payment to COMPLETED and
        advance to PAYMENT_VERIFIED (they skip payment).
        """
        application = UndergraduateApplication(
            applicant_id=actor_id,
            sponsorship_type=data.sponsorship_type,
            stream=data.stream,
            admission_number=data.admission_number,
            program_choice_1_id=data.program_choice_1_id,
            program_choice_2_id=data.program_choice_2_id,
            program_choice_3_id=data.program_choice_3_id,
            admission_term_id=data.admission_term_id,
            current_status=ApplicationStatus.DRAFT,
            extra_data=data.extra_data,
        )

        # Government-sponsored: auto-complete payment
        if data.sponsorship_type == SponsorshipType.GOVERNMENT:
            application.payment_status = PaymentStatus.COMPLETED
            application.payment_reference = "GOV_SPONSORED_NO_PAYMENT"

        await self._ensure_open_admission_term(data.admission_term_id)
        self._app_repo.add(application)

        try:
            await self._db.flush()  # get the ID, check unique constraint
        except IntegrityError as e:
            await self._db.rollback()
            if self._is_duplicate_term_application_error(e):
                raise DuplicateApplicationError(
                    "You already have an application for this admission term"
                )
            raise

        # Auto-transition DRAFT → SUBMITTED
        await self._transition_status(
            application=application,
            new_status=ApplicationStatus.SUBMITTED,
            actor_id=actor_id,
            actor_role=UserRole.STUDENT,
            trigger_reason="Application submitted by student",
        )

        # SUBMITTED → PAYMENT_PENDING
        await self._transition_status(
            application=application,
            new_status=ApplicationStatus.PAYMENT_PENDING,
            actor_id=actor_id,
            actor_role=UserRole.STUDENT,
            trigger_reason="Awaiting payment",
        )

        # Government-sponsored: auto-advance past payment
        if data.sponsorship_type == SponsorshipType.GOVERNMENT:
            await self._transition_status(
                application=application,
                new_status=ApplicationStatus.PAYMENT_VERIFIED,
                actor_id=actor_id,
                actor_role=UserRole.SYSTEM,
                trigger_reason="Government-sponsored — payment not required",
            )

        await self._db.commit()
        await self._db.refresh(application)
        return application

    # ── Payment Operations ────────────────────────────────────

    async def initiate_payment(
        self, application_id: uuid.UUID, actor_id: uuid.UUID
    ) -> UndergraduateApplication:
        """
        Generate a simulated payment reference for a self-sponsored application.
        Application must be in PAYMENT_PENDING status.
        """
        application = await self._app_repo.get_by_id(application_id)
        if application is None:
            raise EntityNotFoundError("UndergraduateApplication", str(application_id))

        if application.current_status != ApplicationStatus.PAYMENT_PENDING:
            raise InvalidStateTransitionError(
                application.current_status.value, "Payment can only be initiated in PAYMENT_PENDING"
            )

        if application.sponsorship_type == SponsorshipType.GOVERNMENT:
            raise MissingPrerequisiteError(
                "Government-sponsored applications do not require payment"
            )

        # Generate simulated payment reference
        application.payment_reference = f"PAY-{uuid.uuid4().hex[:12].upper()}"
        await self._db.commit()
        await self._db.refresh(application)
        return application

    async def complete_payment(
        self, application_id: uuid.UUID, payment_reference: str
    ) -> UndergraduateApplication:
        """
        Simulated payment callback — marks payment as complete and
        transitions to PAYMENT_VERIFIED.
        """
        application = await self._app_repo.get_by_id(application_id)
        if application is None:
            raise EntityNotFoundError("UndergraduateApplication", str(application_id))

        if application.current_status != ApplicationStatus.PAYMENT_PENDING:
            raise InvalidStateTransitionError(
                application.current_status.value, "Payment callback only valid in PAYMENT_PENDING"
            )

        if application.payment_reference != payment_reference:
            raise MissingPrerequisiteError("Payment reference does not match")

        application.payment_status = PaymentStatus.COMPLETED

        await self._transition_status(
            application=application,
            new_status=ApplicationStatus.PAYMENT_VERIFIED,
            actor_id=application.applicant_id,
            actor_role=UserRole.SYSTEM,
            trigger_reason="Payment completed successfully",
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

    async def to_application_response(
        self, application: UndergraduateApplication
    ) -> ApplicationResponse:
        """Map one application ORM row to API response (UAT + program enrichment)."""
        built = await self._build_application_responses((application,))
        return built[0]

    async def list_applications(
        self, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[ApplicationResponse], int]:
        items, total = await self._app_repo.get_all(limit=limit, offset=offset)
        enriched = await self._build_application_responses(items)
        return enriched, total

    async def list_my_applications(
        self, applicant_id: uuid.UUID
    ) -> list[ApplicationResponse]:
        items = await self._app_repo.get_applications_for_applicant(applicant_id)
        return await self._build_application_responses(items)

    async def has_application_for_term(
        self, applicant_id: uuid.UUID, admission_term_id: uuid.UUID
    ) -> bool:
        return await self._app_repo.exists_for_applicant_and_term(
            applicant_id=applicant_id,
            admission_term_id=admission_term_id,
        )

    async def _build_application_responses(
        self, applications: Sequence[UndergraduateApplication]
    ) -> list[ApplicationResponse]:
        """Batch-load UAT ids and program rows for application responses."""
        if not applications:
            return []

        app_ids = [a.id for a in applications]
        term_ids = {a.admission_term_id for a in applications}

        term_result = await self._db.execute(
            select(UndergraduateAdmissionTerm).where(
                UndergraduateAdmissionTerm.id.in_(term_ids),
                UndergraduateAdmissionTerm.is_deleted == False,  # noqa: E712
            )
        )
        term_map = {t.id: t for t in term_result.scalars().all()}

        uat_result = await self._db.execute(
            select(UATRecord).where(UATRecord.application_id.in_(app_ids))
        )
        uat_rows = uat_result.scalars().all()
        latest_uat: dict[uuid.UUID, UATRecord] = {}
        for r in uat_rows:
            existing = latest_uat.get(r.application_id)
            if existing is None or r.created_at > existing.created_at:
                latest_uat[r.application_id] = r
        uat_id_by_app = {aid: rec.uat_id for aid, rec in latest_uat.items()}

        prog_ids: set[uuid.UUID] = set()
        for a in applications:
            if a.sponsorship_type == SponsorshipType.SELF_SPONSORED:
                for pid in (
                    a.program_choice_1_id,
                    a.program_choice_2_id,
                    a.program_choice_3_id,
                ):
                    if pid is not None:
                        prog_ids.add(pid)

        prog_map: dict[uuid.UUID, AcademicProgram] = {}
        if prog_ids:
            prog_result = await self._db.execute(
                select(AcademicProgram).where(
                    AcademicProgram.id.in_(prog_ids),
                    AcademicProgram.is_deleted == False,  # noqa: E712
                )
            )
            for p in prog_result.scalars().all():
                prog_map[p.id] = p

        def choice_summary(pid: Optional[uuid.UUID]) -> Optional[ProgramChoiceSummary]:
            if pid is None:
                return None
            prog = prog_map.get(pid)
            if prog is None:
                return None
            return ProgramChoiceSummary(id=prog.id, code=prog.code, name=prog.name)

        out: list[ApplicationResponse] = []
        for a in applications:
            term = term_map.get(a.admission_term_id)
            if a.sponsorship_type == SponsorshipType.SELF_SPONSORED:
                p1 = choice_summary(a.program_choice_1_id)
                p2 = choice_summary(a.program_choice_2_id)
                p3 = choice_summary(a.program_choice_3_id)
            else:
                p1 = p2 = p3 = None

            out.append(
                ApplicationResponse(
                    id=a.id,
                    applicant_id=a.applicant_id,
                    sponsorship_type=a.sponsorship_type,
                    stream=a.stream,
                    admission_number=a.admission_number,
                    program_choice_1=p1,
                    program_choice_2=p2,
                    program_choice_3=p3,
                    admission_term=ApplicationResponse.AdmissionTermSummary(
                        id=a.admission_term_id,
                        term_name=term.term_name if term else "Unknown",
                    ),
                    current_status=a.current_status,
                    final_decision=a.final_decision,
                    payment_status=a.payment_status,
                    payment_reference=a.payment_reference,
                    remarks=a.remarks,
                    extra_data=a.extra_data,
                    is_deleted=a.is_deleted,
                    created_at=a.created_at,
                    updated_at=a.updated_at,
                    uat_id=uat_id_by_app.get(a.id),
                )
            )
        return out

    async def create_admission_term(self, data: AdmissionTermCreate) -> UndergraduateAdmissionTerm:
        term = UndergraduateAdmissionTerm(
            term_name=data.term_name,
            start_date=data.start_date,
            end_date=data.end_date,
            is_open=data.is_open,
            description=data.description,
        )
        self._db.add(term)
        await self._db.commit()
        await self._db.refresh(term)
        return term

    async def list_open_admission_terms(self) -> list[AdmissionTermResponse]:
        result = await self._db.execute(
            select(UndergraduateAdmissionTerm).where(
                UndergraduateAdmissionTerm.is_deleted == False,  # noqa: E712
                UndergraduateAdmissionTerm.is_open == True,  # noqa: E712
            ).order_by(UndergraduateAdmissionTerm.start_date.asc())
        )
        return [AdmissionTermResponse.model_validate(t) for t in result.scalars().all()]

    async def _ensure_open_admission_term(self, term_id: uuid.UUID) -> None:
        result = await self._db.execute(
            select(UndergraduateAdmissionTerm).where(
                UndergraduateAdmissionTerm.id == term_id,
                UndergraduateAdmissionTerm.is_deleted == False,  # noqa: E712
                UndergraduateAdmissionTerm.is_open == True,  # noqa: E712
            )
        )
        if result.scalar_one_or_none() is None:
            raise MissingPrerequisiteError("Selected admission term is not open or does not exist")

    @staticmethod
    def _is_duplicate_term_application_error(error: IntegrityError) -> bool:
        err_text = f"{error}\n{getattr(error, 'orig', '')}"
        return (
            "uq_one_app_per_term" in err_text
            or "undergraduate_applications_applicant_id_admission_term_id_key" in err_text
        )

    async def get_review_queue(
        self, *, limit: int = 50, offset: int = 0
    ) -> list[ApplicationResponse]:
        items = await self._app_repo.get_pending_review_queue(
            limit=limit, offset=offset
        )
        return await self._build_application_responses(items)

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
