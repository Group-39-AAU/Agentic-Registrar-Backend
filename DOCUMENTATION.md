# Agentic Registrar Backend – System Documentation

This document provides a comprehensive overview of the **Agentic Registrar** backend monorepo for Addis Ababa University. It describes the monorepo layout, shared libraries, individual services, LangGraph-based workflows, infrastructure, and testing approach.

The frontend lives in a separate repository; this monorepo is backend-only.

---

## 1. Monorepo Structure

Root directory: `agentic-registrar-backend/`

- `services/`
  - `api-gateway/` – external entrypoint and reverse proxy.
  - `identity-service/` – authentication, authorization, identity.
  - `undergrad-admission-service/` – undergraduate admissions workflows.
  - `graduate-admission-service/` – graduate admissions workflows.
  - `course-management-service/` – course registration, grading, standing.
  - `notification-service/` – notifications (email/SMS, mocked).
  - `document-service/` – document metadata and storage integration.
- `packages/`
  - `shared-kernel/` – cross-cutting utilities and primitives.
  - `contracts/` – shared DTOs, response envelopes, enums.
  - `agent-core/` – agent & LangGraph-oriented abstractions.
  - `policy-engine/` – deterministic academic and policy helpers.
  - `rag-core/` – RAG abstractions and mock implementations.
  - `test-utils/` – shared testing helpers and fixtures.
- `db/` – shared migrations/seeds/schemas (currently placeholders).
- `infra/` – docker/compose/nginx/monitoring/scripts scaffolding.
- `docs/` – architecture, ADR, API, and workflow documentation (to be expanded).

Key top-level files:

- `pyproject.toml` – root package + workspace dependencies and tooling config.
- `Makefile` – common tasks (install, lint, test, docker up/down, run-*) .
- `.env.example` – environment variable template.
- `README.md` – high-level monorepo overview.
- `DOCUMENTATION.md` – this document.

---

## 2. Shared Packages

### 2.1 `shared-kernel`

Purpose: cross-cutting concerns that **must not** depend on any specific service domain.

Main modules:

- `config.py`
  - `AppSettings(BaseSettings)` – generic application config (database, MinIO, JWT, environment).
  - `get_settings()` – cached settings accessor.
- `logging.py`
  - `configure_logging(level="INFO")` – installs a structured root logger.
  - `get_logger(name=None)` – logger accessor.
  - Output is key=value style with UTC timestamps and optional `correlation_id`.
- `exceptions.py`
  - `AppError` – base error with `code` and `details`.
  - Specific subclasses: `NotFoundError`, `ValidationError`, `ConflictError`, `UnauthorizedError`, `ForbiddenError`, `DependencyError`.
- `api.py`
  - `ErrorDetail`, `ErrorResponse`, `HealthStatus` – Pydantic models for consistent API output.
  - `make_error_response(HTTPStatus, code, message, target=None)` – helper for error responses.
- `correlation.py`
  - `CorrelationIdMiddleware` – ASGI middleware that:
    - Reads/sets `X-Request-ID`.
    - Stores correlation ID in a `contextvars.ContextVar`.
    - Adds `X-Request-ID` on HTTP responses.
  - `get_correlation_id()` / `set_correlation_id()` – correlation accessors.
- `health.py`
  - `make_health_status(status="ok", details=None) -> HealthStatus`.
- `security.py`
  - `PasswordHasher` protocol.
  - `PBKDF2PasswordHasher` – stdlib-based PBKDF2-SHA256 implementation.
  - `JWTConfig`, `JWTHelper` protocol, `JWTError` – JWT abstractions (no concrete JWT lib baked in).
  - `compute_expiry(ttl)` – returns UTC expiry timestamps.
- `db.py`
  - `Base(DeclarativeBase)` – SQLAlchemy 2.x ORM base class.
  - `TimestampMixin` – `created_at` / `updated_at` with defaults and server timestamps.
  - `UUID_PK` – convenience annotated UUID primary key column type.
- `utils.py`
  - `utc_now()`, `generate_uuid()`, `chunked(iterable, size)` – general helpers.

### 2.2 `contracts`

Purpose: shared contracts and DTOs for requests/responses and events.

Components:

- `enums.py` – `Environment`, `SortOrder`, `AuditAction`, `AuthTokenType`, `AcademicStanding`.
- `responses.py`
  - `ResponseMeta` – `request_id`, `success`, `message`.
  - `ResponseEnvelope[T]` – generic envelope (`meta`, `data`, `error`).
- `pagination.py` – `PageMeta`, `PaginatedResponse[T]`.
- `audit.py` – `AuditLogEntry` (actor, action, resource, timestamp, metadata).
- `events.py` – `EventEnvelope` (id, type, source, payload, occurred_at, correlation_id).
- `auth.py` – `AuthTokenPayload` (JWT `sub`, `iat`, `exp`, type, scopes, roles).
- `dto.py` – `UserRef`, `ProgramRef`, `CourseRef`, `AuditInfo`.

These are used where services need a common vocabulary for responses and internal events.

### 2.3 `agent-core`

Purpose: abstractions for agent-like components and LangGraph state management.

Key parts:

- `base.py`
  - `BaseAgent[StateT, ResultT]` – async `run(state, **kwargs)` contract.
  - `ContextAwareAgent` – extends `run_with_context(state, context, **kwargs)`.
- `models.py`
  - `GraphExecutionMetadata` – run id, graph name, timestamps, status, metadata.
  - `HumanInTheLoopCheckpoint` – HITL checkpoint representation.
  - `GuardrailResult` – validation results for outputs (valid, reasons, optional corrected output).
- `state.py`
  - `GraphContext` – correlation id + metadata.
  - `GraphState[StateT]` – generic shaped state with `data`, `context`, `history`.
  - `merge_state(current, update)`, `append_history_entry(history, entry)` – functional helpers.
- `retry.py`
  - `retry` – sync retry decorator using exponential backoff.
  - `async_retry` – async retry decorator using `asyncio.sleep`.
  - `RetryError` – raised if attempts are exhausted.
- `utils.py`
  - `merge_metadata`, `shorten_id`.

### 2.4 `policy-engine`

Purpose: deterministic academic rules and evaluations.

Key modules:

- `models.py` – `PolicyVersion` metadata.
- `admissions.py`
  - `ApplicantScore` dataclass, `meets_cutoff`, `rank_applicants`, `top_n_applicants`.
- `prerequisites.py`
  - `has_completed_prerequisites`, `missing_prerequisites` (case-insensitive).
- `credit.py`
  - `validate_credit_load(current, additional, min_credits, max_credits)` – ensures final credits within range.
- `gpa.py`
  - `calculate_gpa(grades, credits)` – weighted average.
  - `calculate_cgpa(term_gpas, term_credits)` – cross-term weighted average.
- `standing.py`
  - `StandingThresholds` – thresholds for GOOD/PROBATION/SUSPENDED/DISMISSED.
  - `evaluate_academic_standing(gpa, thresholds)` – deterministic mapping to `AcademicStanding`.

### 2.5 `rag-core`

Purpose: RAG abstractions with simple, deterministic in-memory mocks.

Includes:

- `schemas.py`
  - `ChunkMetadata`, `RetrievedChunk`, `Citation`, `RetrievalResult`.
- `interfaces.py`
  - `EmbeddingProvider`, `Retriever`, `IndexableDocument` ABCs.
- `mock.py`
  - `InMemoryEmbeddingProvider` – character-hash embeddings.
  - `InMemoryRetriever` – simple in-memory similarity search.

### 2.6 `test-utils`

Purpose: testing helpers and fixtures usable across all services.

Components:

- `pytest_helpers.py` – markers (`mark_integration`, `mark_slow`), `parametrize_ids`.
- `factories.py` – factories for `UserRef`, `AuditLogEntry`, `EventEnvelope`, `AuthTokenPayload`.
- `db.py`
  - `create_sqlite_memory_engine`, `create_test_session_factory`.
  - `session_scope(SessionFactory)` – context-like generator for tests.
- `api_client.py`
  - `create_async_client(app)`, `lifespan_client(app)` for HTTPX-in-tests.

---

## 3. Services

### 3.1 API Gateway (`services/api-gateway`)

**Purpose:** External entrypoint and reverse proxy to internal services.

Structure:

- `app/main.py` – FastAPI app factory `create_app()`:
  - Configures logging and `CorrelationIdMiddleware`.
  - Includes:
    - `api/health.py` → `/health`.
    - `api/routes.py` → proxy groups.
- `app/config/settings.py` – `GatewaySettings` for downstream URLs.
- `app/infrastructure/http_client.py` – `GatewayHttpClient` with correlation propagation.
- `app/api/health.py` – health endpoint.
- `app/api/auth.py` – JWT bearer auth + token introspection with identity-service.
- `app/api/routes.py` – proxy route groups:
  - `/auth/*` → identity service.
  - `/undergrad/*` → undergrad-admission.
  - `/graduate/*` → graduate-admission.
  - `/courses/*` → course-management.
  - `/documents/*` → document-service.
  - `/notifications/*` → notification-service.

JWT validation:

- `get_current_principal` uses `HTTPBearer` and calls identity-service `/auth/token/introspect` with the token.
- Fails closed on invalid/expired tokens.

Proxy behavior:

- Requests are forwarded using a shared async `httpx` client with `X-Request-ID` propagated from correlation middleware.
- Downstream **status codes and JSON bodies are preserved** using `JSONResponse`, with hop-by-hop headers stripped.
- The proxy currently focuses on JSON payloads and non-streaming use cases.

### 3.2 Identity Service (`services/identity-service`)

**Purpose:** Authentication, roles, JWT, and role-based context for other services.

Structure:

- `app/config/settings.py` – `IdentitySettings`:
  - DB connection.
  - JWT secret, algorithm, expiry.
  - Optional bootstrap admin (`IDENTITY_ADMIN_*` envs).
- `app/domain/models.py` – SQLAlchemy models in schema `identity`:
  - `User` – `username`, `email`, `hashed_password`, `is_active`, `status`, `roles`.
  - `Role` – `name`, `type`.
  - `UserRole` – join table.
- `app/domain/schemas.py` – Pydantic DTOs: `UserOut`, `RoleOut`, `LoginRequest`, `TokenResponse`, `TokenIntrospect*`.
- `app/infrastructure/db.py` – engine + `SessionLocal` + `get_db_session`.
- `app/infrastructure/repositories.py` – `UserRepository`, `RoleRepository`.
- `app/infrastructure/security.py`
  - `StdlibJWTHelper` – minimal HS256 JWT encode/decode via stdlib.
  - `create_password_hasher()`, `create_jwt_helper()`, `build_access_token_payload(...)`.
- `app/application/services.py`
  - `AuthService` – authenticate user (username/email + password).
  - `SeedService` – ensure default roles and optional admin user.
- `app/api/rest/health.py` – `/health`.
- `app/api/rest/auth.py` – auth endpoints:
  - `POST /auth/login` – issues access token in `ResponseEnvelope[TokenResponse]`.
  - `POST /auth/token/introspect` – returns `TokenIntrospectResponse`.
  - `GET /auth/me` – returns a basic `UserOut` (currently derived from token).
- `app/main.py` – `create_app()`:
  - Configures logging and correlation middleware.
  - On startup:
    - Does **not** auto-create tables; production deployments are expected to run migrations.
    - Seeds roles and optional admin user via `SeedService` where appropriate.

### 3.3 Document Service (`services/document-service`)

**Purpose:** Document metadata lifecycle and object-storage integration.

Structure:

- `app/config/settings.py` – `DocumentSettings` (DB + MinIO).
- `app/domain/models.py` – `Document` in schema `documents`:
  - owner info, type, metadata fields, `upload_status`, `verification_status`.
- `app/domain/schemas.py` – `DocumentOut`, `InitiateUploadRequest/Response`, `CompleteUploadRequest`.
- `app/infrastructure/db.py` – engine/session.
- `app/infrastructure/storage.py` – `StorageClient` and `MockS3StorageClient`:
  - Generates deterministic `storage_path` and `upload_url`.
- `app/infrastructure/repositories.py` – `DocumentRepository`.
- `app/application/services.py` – `DocumentService` with:
  - `initiate_upload()`, `complete_upload()`, `get_document()`.
- `app/api/rest`:
  - `health.py` – `/health`.
  - `documents.py` – endpoints:
    - `POST /documents/initiate-upload`.
    - `POST /documents/complete-upload`.
    - `GET /documents/{id}`.
  - All responses wrapped in `ResponseEnvelope`.
- `app/main.py` – app factory + startup `create_all`.
  - Startup does **not** call `create_all`; database schema should be managed via migrations.

### 3.4 Notification Service (`services/notification-service`)

**Purpose:** Send and track notifications via pluggable providers.

Structure:

- `app/config/settings.py` – DB config.
- `app/domain/models.py` – `Notification` in schema `notifications`:
  - `channel`, `recipient`, `subject`, `body`, `template_key`, `status`, `retry_count`, `last_error`.
- `app/domain/schemas.py` – `SendNotificationRequest`, `NotificationOut`.
- `app/infrastructure/db.py` – engine/session.
- `app/infrastructure/providers.py` – providers:
  - `NotificationProvider` ABC.
  - `MockEmailProvider`, `MockSmsProvider`.
  - `get_provider(channel)` helper.
- `app/infrastructure/repositories.py` – `NotificationRepository`.
- `app/application/services.py` – `NotificationService`:
  - `send()` – calls provider, updates `status` and `retry_count`.
  - `get_notification(id)` – returns `NotificationOut`.
- `app/api/rest`:
  - `health.py` – `/health`.
  - `notifications.py` – `POST /notifications/send`, `GET /notifications/{id}`.
- `app/main.py` – app factory + startup `create_all`.
  - Startup does **not** call `create_all`; database schema should be managed via migrations.

### 3.5 Undergrad Admission Service (`services/undergrad-admission-service`)

**Purpose:** Orchestrate undergraduate admissions workflows with deterministic policies and LangGraph.

Structure:

- `app/config/settings.py` – `UndergradAdmissionSettings`:
  - DB.
  - Score cutoffs, UAT/CGPA weighting for cumulative score.
- `app/domain/enums.py` – `ApplicationStatus`, `VerificationOutcome`, `Channel`.
- `app/domain/entities/models.py` – schema `undergrad_admission`:
  - `ApplicantProfile`, `UndergraduateApplication`, `ApplicationDocumentReference`, `VerificationResult`, `RankingResult`, `HumanReviewCheckpoint`.
- `app/domain/repositories/applications.py`
  - `ApplicantRepository`, `ApplicationRepository`, `ApplicationDocumentRepository`.
- `app/application/dto/models.py` – DTOs:
  - `ApplicantCreateRequest/Response`, `ApplicationCreateRequest/Response`, list response, `ApplicationDocumentCreateRequest`, `OfficerDecisionRequest`.
- `app/application/services/app_service.py` – `UndergradApplicationService`:
  - Create applicant & application (with audit logging via `shared_kernel.db.write_audit_event`).
  - Attach documents.
  - Submit application (status change), enforcing valid transitions (e.g., only from `DRAFT`/`VALIDATION_PENDING`) and writing audit events.
- `app/api/rest`:
  - `health.py` – `/health`.
  - `applications.py` – endpoints:
    - `POST /applications/applicants`.
    - `POST /applications`.
    - `POST /applications/{id}/documents`.
    - `POST /applications/{id}/submit`.
    - `GET /applications/{id}`.
    - `GET /applications`.
    - `POST /applications/{id}/officer-decision` (currently DTO-level status update).
- `app/state/models.py` – `UndergradWorkflowState`.
- `app/graphs/undergrad_main_graph.py` – LangGraph workflow:
  - Nodes:
    - `load_application`, `validate_completeness`, `verify_payment`, `extract_document_data`, `verify_with_moe`, `fetch_uat_score`, `compute_cumulative_score`, `rank_candidate`, `create_human_review_checkpoint`, `finalize_decision`, `trigger_onboarding`.
  - Deterministic branching based on flags in `state.data`:
    - Payment failures (`force_payment_failure`).
    - Extraction issues (`force_extraction_issue`).
    - MoE mismatches (`force_moe_mismatch`).
    - Human review flags (`flagged_for_review`).
  - Status transitions use `ApplicationStatus` (e.g., `VALIDATION_PENDING`, `VERIFICATION_PENDING`, `FLAGGED_FOR_REVIEW`, `RECOMMENDED`, `REJECTED`, `ONBOARDED`).
- Tests:
  - `tests/test_basic.py` – HTTP health + CRUD tests with SQLite.
  - `tests/test_graph.py` – graph branching tests.

### 3.6 Graduate Admission Service (`services/graduate-admission-service`)

**Purpose:** Graduate admissions with deterministic evaluation, department routing, and LangGraph orchestration.

Structure:

- `app/config/settings.py` – `GraduateAdmissionSettings`:
  - DB.
  - GAT/transcript weights.
  - Department capacity, cutoffs.
- `app/domain/enums.py` – `ApplicationStatus`, `VerificationOutcome`.
- `app/domain/entities.py` – schema `graduate_admission`:
  - `GraduateApplicantProfile`, `GraduateApplication`, `GraduateDocumentReference`,
    `DepartmentEvaluationResult`, `DepartmentReviewCheckpoint`, `RegistrarReviewCheckpoint`, `EnrollmentResult`.
- `app/infrastructure/db.py` – engine/session.
- `app/infrastructure/clients.py` – ABCs + mocks:
  - `PaymentProvider`, `GATResultProvider`, `TranscriptVerificationProvider`, `DocumentServiceClient`, `NotificationServiceClient`.
- `app/application/dto.py` – applicant, application, document, and decision DTOs.
- `app/application/services.py` – `GraduateApplicationService`:
  - Applicant and application lifecycle (without deep domain rules).
- `app/api/rest`:
  - `health.py` – `/health`.
  - `applications.py` – endpoints:
    - `POST /applicants`, `POST /applications`, `POST /applications/{id}/documents`, `POST /applications/{id}/submit`,
      `GET /applications/{id}`, `GET /applications`.
    - Department/registrar decisions: `POST /applications/{id}/department-decision`, `POST /applications/{id}/registrar-decision`, with validation of allowed status transitions (e.g., registrar decision only after department approval).
- `app/state/models.py` – `GraduateWorkflowState`.
- `app/graphs/graduate_main_graph.py` – LangGraph workflow:
  - Nodes:
    - `load_application`, `validate_fields`, `verify_payment`, `fetch_gat_result`, `extract_transcript_data`, `verify_transcript`, `route_to_department`, `evaluate_department_fit`, `apply_capacity_filter`, `create_department_review_checkpoint`, `create_registrar_review_checkpoint`, `finalize_decision`, `trigger_onboarding`.
  - Asynchronous nodes (`verify_payment`, `fetch_gat_result`, `verify_transcript`) wrapped with `@async_retry` for robustness, but decisions are still deterministic via flags and provider mocks.
  - Branching:
    - Payment failure, transcript verification failure, and capacity failure paths lead to `REJECTED`.
    - Successful paths lead to `APPROVED` then `ENROLLED`.
- Tests:
  - `tests/test_basic.py` – health + basic CRUD tests.
  - `tests/test_graph.py` – graph happy path and failure path coverage.

### 3.7 Course Management Service (`services/course-management-service`)

**Purpose:** Course registration, add/drop, grading, academic standing, and academic records.

Structure:

- `app/config/settings.py` – `CourseManagementSettings` with DB and `min_credits` / `max_credits`.
- `app/domain/models.py` – schema `course_management`:
  - `Registration`, `RegistrationItem`, `AddDropRequest`, `GradeSubmission`, `GradeEntry`,
    `AcademicStanding`, `AcademicRecord`, `ManualExceptionCase`.
- `app/infrastructure/db.py` – engine/session.
- `app/infrastructure/clients.py` – payment, notification, and identity clients (mocked).
- `app/application/dto.py` – DTOs for registrations, add/drop, grades, and standings.
- `app/application/services.py`:
  - `RegistrationService`:
    - `create_registration` – builds `Registration` and `RegistrationItem`s, with audit logging.
    - `validate_registration` – uses `policy_engine.credit.validate_credit_load` to enforce min/max credits; opens `ManualExceptionCase` if out of range; sets status accordingly; only allows validation from `draft`/`exception` statuses; logs audit events.
    - `finalize_registration` – only allowed from `validated` status; sets status to `finalized` and logs an audit event.
    - `add_drop` – adjusts credits and stores `AddDropRequest`.
  - `GradeService`:
    - `submit_grades` – creates `GradeSubmission` and `GradeEntry`s.
    - `authorize_submission` – marks `GradeSubmission` as `authorized` and logs an audit event.
  - `StandingService`:
    - `compute_standing` – uses `calculate_gpa` / `calculate_cgpa` and `evaluate_academic_standing` to derive standings; stores `AcademicStanding`.
    - `authorize_standing` – marks standing as authorized and logs an audit event.
  - `RecordService`:
    - `generate_record` – creates an `AcademicRecord` with a generated payload and logs an audit event.
    - `resolve_exception` – resolves `ManualExceptionCase` and logs an audit event.
- `app/api/rest/health.py` – `/health`.
- `app/api/rest/registrations.py` – endpoints:
  - `POST /registrations`, `POST /registrations/{id}/validate`, `POST /registrations/{id}/finalize`.
  - `POST /add-drop`.
  - `POST /grades/submit`, `POST /grades/{id}/authorize`.
  - `POST /standing/compute`, `POST /standing/{id}/authorize`.
  - `POST /records/generate`.
  - `POST /exceptions/{case_id}/resolve`.
- `app/state/models.py` – `CourseWorkflowState`.
- `app/graphs/course_management_main_graph.py` – LangGraph workflow:
  - Nodes: `load_registration`, `validate_prerequisites`, `validate_payment_or_cost_sharing`, `evaluate_advisory`,
    `finalize_registration`, `process_add_drop`, `monitor_grade_entry`, `validate_grade_submission`,
    `create_grade_authorization_checkpoint`, `compute_academic_status`, `create_status_authorization_checkpoint`,
    `generate_academic_record`.
  - Sequential graph modeling the overall academic lifecycle for a registration.
- Tests:
  - `tests/test_basic.py` – health + registration lifecycle.
  - `tests/test_graph.py` – basic graph progression.

---

## 4. LangGraph Workflows

LangGraph is used to orchestrate deterministic workflows in:

- Undergrad admissions (`undergrad_main_graph`).
- Graduate admissions (`graduate_main_graph`).
- Course management (`course_management_main_graph`).

### Common Patterns

- State objects extend `agent_core.state.GraphState` with:
  - `data: dict[str, Any]` – business state.
  - `context: GraphContext` – correlation id + metadata.
  - `history: list[dict[str, Any]]` – audit trail of node steps.
- Each node is:
  - Small, single-purpose.
  - Purely deterministic given its input state (mock external providers and flags drive branches).
  - Responsible for updating `data` and appending a `{ "step": ... }` entry to history.
- Conditional edges:
  - Use simple discriminating functions (e.g., `decide_after_payment`, `decide_after_extraction`), making branches explicit and testable.

### Testing Graphs

Graph tests:

- Build the compiled graph with `build_*_main_graph()`.
- Instantiate a workflow state with initial `data`, `GraphContext()`, and empty `history`.
- Call `graph.invoke(state)` and assert:
  - Final `data` status (e.g., `APPROVED`, `REJECTED`, `ONBOARDED`, `ENROLLED`).
  - Expected `history` steps (e.g., human review checkpoints).

---

## 5. Infrastructure and Configuration

### Environment Variables

Core variables (from `.env.example`):

- `ENVIRONMENT` – `local` / `dev` / `staging` / `prod`.
- Postgres:
  - `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`.
- JWT (identity-service and gateway):
  - `JWT_SECRET`, `JWT_ALGORITHM`, `JWT_ACCESS_TOKEN_EXPIRES_MINUTES`.
- MinIO:
  - `MINIO_ENDPOINT`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `MINIO_BUCKET_DOCUMENTS`.
- Service ports (for docker-compose mapping):
  - `API_GATEWAY_PORT`, `IDENTITY_SERVICE_PORT`, `UNDERGRAD_ADMISSION_SERVICE_PORT`,
    `GRADUATE_ADMISSION_SERVICE_PORT`, `COURSE_MANAGEMENT_SERVICE_PORT`,
    `DOCUMENT_SERVICE_PORT`, `NOTIFICATION_SERVICE_PORT`.
- Per-service extra settings:
  - Undergrad: `UNDERGRAD_SCORE_CUTOFF`, `UNDERGRAD_UAT_WEIGHT`, `UNDERGRAD_CGPA_WEIGHT`.
  - Graduate: `GRAD_GAT_CUTOFF`, `GRAD_TRANSCRIPT_WEIGHT`, `GRAD_GAT_WEIGHT`, `GRAD_DEPARTMENT_CAPACITY`.
  - Course-management: `COURSE_MIN_CREDITS`, `COURSE_MAX_CREDITS`.

### Docker and Compose

- `docker-compose.yml` defines:
  - `postgres` and `minio`.
  - All services with build contexts and ports.
- Individual `Dockerfile`s in `services/*`:
  - Use `python:3.12-slim`.
  - Install required runtime dependencies.
  - Copy `app/` and run `uvicorn app.main:app`.

---

## 6. Tooling and Quality

### Linting and Formatting

- `ruff` – lint and format (`make format`, `make lint`).
- `mypy` – static type checking.

### Testing

- `pytest` – main test runner.
  - `test-utils` provides shared in-memory DB helpers and factories.
  - Services use dependency overrides to test against SQLite instead of Postgres.

### Makefile Commands

- `make install` – install project in editable mode with `[dev]`.
- `make format` – `ruff format .`.
- `make lint` – `ruff check .` and `mypy .`.
- `make test` – run all tests.
- `make up` / `make down` – `docker compose up -d` / `docker compose down`.
- `make run-*` – dev-mode `uvicorn` for each service.

---

## 7. Extensibility and Future Work

This monorepo is structured to support incremental evolution:

- **LLM/OCR integration**:
  - Undergrad and graduate graphs already isolate extraction and verification steps in distinct nodes and provider interfaces. A future OCR/LLM service can be wired into those nodes without changing API contracts.
- **Real external providers**:
  - Replace mocks in `infrastructure.clients` and `infrastructure.providers` across services with HTTP clients or SDKs for payment, GAT, MoE, notifications, etc.
- **Richer policy engines**:
  - `policy-engine` can be extended with more nuanced rules (e.g., program-specific thresholds, dynamic capacities) while remaining deterministic.
- **Centralized auditing**:
  - `contracts.audit` plus `shared-kernel` logging/correlation pave the way for a centralized audit log service using the existing event and audit models.

The current implementation emphasizes deterministic behavior, strong typing, small and composable modules, and clear boundaries between domains and shared infrastructure. It is designed to be a solid foundation for future, more advanced agentic and AI-assisted registrar capabilities.

