# Undergraduate Admission Module

Manages the complete undergraduate admission lifecycle — from application submission through AI-powered validation, ranking, officer review, and enrollment.

## Module Structure

```
undergraduate/
├── models.py                # Core models (Application, Document, StatusHistory, Decision)
├── schemas.py               # Pydantic request/response schemas
├── service.py               # Business logic (status machine, validation)
├── repository.py            # Database queries
├── router.py                # Main API endpoints (21 endpoints)
├── exceptions.py            # Module-specific HTTP exceptions
├── event_handlers.py        # Subscribes to cross-module events (e.g. UATCompleted)
│
├── agents/                  # AI agents (LangGraph pipelines)
│   ├── intake_agent.py              # Application validation & eligibility check
│   ├── credential_lookup_agent.py   # MoE admission-number + name verification
│   ├── credential_verification_agent.py  # MoE record verification
│   ├── ranking_agent.py             # Score calculation & seat allocation
│   └── enrollment_agent.py          # ID generation & section assignment
│
├── ranking/                 # Ranking & review sub-package
│   ├── models.py            # StreamQuota, RankingResult
│   ├── schemas.py           # Ranking + review schemas
│   ├── router.py            # Ranking trigger & results endpoints
│   └── review_router.py     # Officer review & decision endpoints
│
└── enrollment/              # Enrollment sub-package
    ├── models.py            # Enrollment record
    ├── schemas.py           # Enrollment response schemas
    └── router.py            # Enrollment trigger & list endpoints
```

## Application Lifecycle

```
SUBMITTED → PAYMENT_PENDING → PAYMENT_VERIFIED → DOCUMENTS_UPLOADED
    → DOCUMENTS_VERIFIED → UNDER_AI_REVIEW → (AI_APPROVED | FLAGGED_FOR_REVIEW)
    → UAT_PENDING → UAT_COMPLETED → PENDING_REVIEW → DECIDED → ENROLLED
```

Each transition is enforced by the state machine in `service.py` and logged in `application_status_history`.

## Dependency Modules

| Module | Why It's Needed |
|---|---|
| **auth** | `User` model for applicant identity and `get_current_user` for authentication |
| **programs** | `AcademicProgram` model — applicants select program preferences, ranking assigns programs |
| **moe** | `MoeStudentRecord` — grade 12 scores fetched for ranking and review display |
| **testing_center** | `UATRecord` — UAT scores used in ranking score calculation |
| **ai** (shared) | `AIEvaluation`, `AIExecutionTrace` — stores agent decisions and reasoning traces |
| **shared/enums** | `ApplicationStatus`, `SponsorshipType`, `StreamType`, `DecisionType`, `UserRole` |
| **shared/events** | `UATCompletedEvent` — testing_center publishes, this module subscribes to handle status transition |
| **shared/audit** | `SystemAuditLog` — records sensitive operations |

## AI Agents

### Intake Agent (`intake_agent.py`)
**Trigger**: Automatic after payment is verified
**Pipeline**: `check_eligibility → validate_documents → decide`
- Validates applicant eligibility against MoE records
- Checks document completeness
- Produces `AI_APPROVED` or `FLAGGED_FOR_REVIEW` decision

### Credential Lookup Agent (`credential_lookup_agent.py`)
**Trigger**: Automatic after Intake Agent passes
**Pipeline**: `moe_lookup → name_cross_check → decide`
- Cross-references students' data with MoE records
- Flags discrepancies for human review


### Ranking Agent (`ranking_agent.py`)
**Trigger**: `POST /undergraduate/ranking/run`
**Pipeline**: `gather_data → calculate_scores → rank_applicants → allocate_seats → finalize`
- Calculates composite score: 50% normalized grade 12 + 50% UAT
- Ranks applicants with tie-breaking on UAT score
- Greedy merit-based seat allocation respecting program preferences and stream quotas

### Enrollment Agent (`enrollment_agent.py`)
**Trigger**: `POST /undergraduate/enrollment/run`
**Pipeline**: `gather_admitted → generate_credentials → assign_and_finalize`
- Generates university IDs in `UGR/XXXX/YY` format
- Creates temporary portal passwords
- Auto-assigns sections per program group

## API Endpoints

### Core Application (`/api/v1/undergraduate/...`)

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `POST` | `/applications` | Applicant | Submit a new undergraduate application |
| `GET` | `/applications` | Officer/Admin | List all applications (paginated, filterable) |
| `GET` | `/applications/me` | Applicant | List current user's applications |
| `GET` | `/applications/review-queue` | Officer/Admin | List applications needing review |
| `GET` | `/applications/{id}` | Any | Get application details |
| `PATCH` | `/applications/{id}/status` | Officer/Admin | Manual status transition |
| `GET` | `/applications/{id}/history` | Any | Get full status change history |
| `POST` | `/applications/{id}/payment/initiate` | Applicant | Start payment flow |
| `POST` | `/applications/{id}/payment/callback` | System | Payment confirmation callback; auto-runs Intake and Credential agents |
| `POST` | `/applications/{id}/decision` | Officer/Admin | Record a human decision |
| `GET` | `/applications/{id}/decision` | Any | Get recorded decision |
| `POST` | `/documents` | Applicant | Upload a document |
| `PATCH` | `/documents/{id}/verify` | Officer/Admin | Manually verify a document |

### Ranking (`/api/v1/undergraduate/ranking/...`)

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `POST` | `/run` | Officer/Admin | Trigger the Ranking Agent (batch process) |
| `GET` | `/results/{batch_id}` | Officer/Admin | Get ranked list for a batch |
| `GET` | `/results/{batch_id}/summary` | Officer/Admin | Get batch summary with cutoffs |
| `GET` | `/stream-quotas` | Officer/Admin | List current stream capacities |
| `PUT` | `/stream-quotas/{stream}` | Officer/Admin | Update a stream's capacity |

### Officer Review (`/api/v1/undergraduate/review/...`)

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `GET` | `/students` | Officer/Admin | Paginated list of students awaiting review (filterable by sponsorship) |
| `GET` | `/students/{id}` | Officer/Admin | Detailed review card (scores, preferences, AI recommendation) |
| `POST` | `/decide/{id}` | Officer/Admin | Make single admission decision (ADMIT/REJECT/WAITLIST) |
| `POST` | `/decide/batch` | Officer/Admin | Batch admission decisions |

### Enrollment (`/api/v1/undergraduate/enrollment/...`)

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `POST` | `/run` | Officer/Admin | Trigger Enrollment Agent for all admitted students |
| `GET` | `/{application_id}` | Any | Get enrollment details (university ID, section, etc.) |
| `GET` | `/list/all` | Any | Paginated list of enrolled students |

## Database Models

| Model | Table | Base | Purpose |
|---|---|---|---|
| `UndergraduateApplication` | `undergraduate_applications` | SoftDeleteBase | Core application record |
| `ApplicationDocument` | `application_documents` | Base | Uploaded documents |
| `ApplicationStatusHistory` | `application_status_history` | Base | Immutable status audit trail |
| `RegistrarDecision` | `registrar_decisions` | Base | Human officer decisions |
| `StreamQuota` | `stream_quotas` | SoftDeleteBase | Configurable stream capacities |
| `RankingResult` | `ranking_results` | Base | Per-applicant ranking output |
| `Enrollment` | `enrollments` | Base | University ID, password, section |

## Event Subscriptions

| Event | Published By | Handler | Effect |
|---|---|---|---|
| `UATCompletedEvent` | `testing_center` | `event_handlers.handle_uat_completed` | Transitions application `UAT_PENDING → UAT_COMPLETED` |
