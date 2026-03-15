# Agentic Registrar Backend Monorepo

This repository contains the backend services and shared libraries for the **Agentic Registrar** system for Addis Ababa University. The frontend lives in a separate repository; this monorepo is backend-only.

The goal of this layout is to support multiple FastAPI-based microservices, shared Python packages, centralized database migrations, and infra-as-code for local development and deployment.

## Tech Stack

- **Language**: Python 3.12
- **Web framework**: FastAPI
- **Validation / settings**: Pydantic v2
- **ORM**: SQLAlchemy 2.x
- **Migrations**: Alembic
- **Testing**: pytest
- **Static analysis**: mypy, Ruff
- **Datastore**: PostgreSQL
- **Object storage**: MinIO-compatible (S3 API)
- **Containerization**: Docker + docker-compose

LangGraph will be integrated later for workflow orchestration but is not yet implemented.

## Monorepo Layout

```text
agentic-registrar-backend/
  services/
    api-gateway/
    identity-service/
    undergrad-admission-service/
    graduate-admission-service/
    course-management-service/
    notification-service/
    document-service/
  packages/
    shared-kernel/
    contracts/
    agent-core/
    policy-engine/
    rag-core/
    test-utils/
  db/
    migrations/
    seeds/
    schemas/
  infra/
    docker/
    compose/
    nginx/
    monitoring/
    scripts/
  docs/
    architecture/
    adr/
    api/
    workflows/
```

### Services

- **api-gateway**: Edge FastAPI service that fronts all internal services, handles request routing, API composition, and cross-cutting concerns (auth, rate limiting, observability).
- **identity-service**: Authentication, authorization, user accounts, roles/permissions, JWT issuance/validation, and future identity provider integrations.
- **undergrad-admission-service**: Undergraduate admissions workflows, applications, applicant data, status tracking, and related policies (to be implemented).
- **graduate-admission-service**: Graduate admissions workflows parallel to undergrad with program-specific rules and approval flows.
- **course-management-service**: Courses, sections, schedules, prerequisites, enrolment rules, and registrar-managed academic structures.
- **notification-service**: Email/SMS/other channel notifications, templates, dispatch, and delivery tracking.
- **document-service**: Document upload, storage, retrieval, and integration with MinIO-compatible object storage.

Each service is a standalone FastAPI app with its own `app/`, `tests/`, `Dockerfile`, and a minimal `pyproject.toml` if needed later.

### Packages

- **shared-kernel**: Cross-cutting domain primitives and value objects that are stable across bounded contexts.
- **contracts**: API contracts, Pydantic models, and shared schemas for inter-service communication.
- **agent-core**: Core abstractions and utilities for agentic workflows (LangGraph integration to be added later).
- **policy-engine**: Policy evaluation logic, rules, and decision helpers shared across services.
- **rag-core**: Retrieval-augmented generation components and utilities for AI-assisted registrar flows.
- **test-utils**: Shared testing helpers, fixtures, and utilities for services and packages.

### Database and Infra

- **db/**: Centralized database migrations (`alembic`), seeds, and schema documentation.
- **infra/**: Dockerfiles, docker-compose files, NGINX configs, monitoring configuration, and helper scripts.
- **docs/**: Architecture decision records, high-level system design documentation, API docs, and (future) workflow diagrams.

## Getting Started (Local Development)

1. **Install Python dependencies** (inside `agentic-registrar-backend/`):

   ```bash
   python -m pip install --upgrade pip
   python -m pip install -e .[dev]
   ```

2. **Copy and edit environment variables**:

   ```bash
   cp .env.example .env
   # edit .env as needed
   ```

3. **Start infrastructure and service containers**:

   ```bash
   docker compose up -d
   ```

4. **Run a service locally (example: identity-service)**:

   ```bash
   make run-identity
   ```

   Health endpoints are available at:

   - `api-gateway`: `http://localhost:8000/health`
   - `identity-service`: `http://localhost:8001/health`
   - `undergrad-admission-service`: `http://localhost:8002/health`
   - `graduate-admission-service`: `http://localhost:8003/health`
   - `course-management-service`: `http://localhost:8004/health`
   - `document-service`: `http://localhost:8005/health`
   - `notification-service`: `http://localhost:8006/health`

## Tooling

- **Formatting & linting**:

  ```bash
  make format
  make lint
  ```

- **Tests**:

  ```bash
  make test
  ```

This repository is intentionally minimal at this stage: service skeletons, infrastructure scaffolding, and tooling configuration only. Business logic and workflows will be implemented incrementally.

