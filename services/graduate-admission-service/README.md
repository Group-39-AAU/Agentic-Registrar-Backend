# Graduate Admission Service

FastAPI-based graduate admission service for the Agentic Registrar system.

## Responsibilities

- Graduate applicant and application management.
- Transcript/document reference attachment.
- Validation, payment verification, and GAT result integration.
- Department routing, evaluation, and ranking.
- Department head and registrar approval checkpoints.
- Onboarding trigger and notifications (mocked).

## Endpoints

- `GET /health`
- `POST /applicants`
- `POST /applications`
- `POST /applications/{application_id}/documents`
- `POST /applications/{application_id}/submit`
- `GET /applications/{application_id}`
- `GET /applications`
- `POST /applications/{application_id}/department-decision`
- `POST /applications/{application_id}/registrar-decision`

## Status lifecycle

The `ApplicationStatus` enum covers:

`DRAFT -> SUBMITTED -> VALIDATION_PENDING -> VALIDATED -> GAT_VERIFIED
-> TRANSCRIPT_VERIFICATION_PENDING -> TRANSCRIPT_VERIFIED -> ROUTED_TO_DEPARTMENT
-> DEPARTMENT_REVIEW -> DEPARTMENT_APPROVED -> REGISTRAR_REVIEW
-> APPROVED/REJECTED -> ENROLLED`

## Workflow

- A LangGraph-based `graduate_main_graph` orchestrates the main flow with nodes for:
  - loading the application
  - validating fields
  - payment verification
  - fetching GAT results
  - transcript extraction and verification
  - routing to department and evaluating fit
  - applying deterministic capacity rules
  - creating department/registrar review checkpoints
  - final decision and onboarding trigger

All external integrations (payment, GAT, transcript verification, document service, notification service) are currently mocked behind pluggable interfaces in `app/infrastructure/clients.py`.


