# Course Management Service

FastAPI-based course management service for the Agentic Registrar system.

## Responsibilities

- Course registration and add/drop.
- Prerequisite and credit-load validation.
- Grade submission and authorization.
- Academic standing computation and authorization.
- Academic record generation and manual exception handling.

## Endpoints

- `GET /health`
- `POST /registrations`
- `POST /registrations/{registration_id}/validate`
- `POST /registrations/{registration_id}/finalize`
- `POST /add-drop`
- `POST /grades/submit`
- `POST /grades/{submission_id}/authorize`
- `POST /standing/compute`
- `POST /standing/{standing_id}/authorize`
- `POST /records/generate`
- `POST /exceptions/{case_id}/resolve`

## Workflow

- A LangGraph-based `course_management_main_graph` orchestrates the registration and academic status workflow:
  - load registration
  - validate prerequisites and credit rules
  - validate payment/cost sharing
  - evaluate academic advising outcomes
  - finalize registration and add/drop adjustments
  - monitor grade entry and validate submissions
  - create authorization checkpoints for grades and academic standing
  - compute academic status and generate academic records

Policy decisions for credit load, GPA/CGPA, and academic standing are delegated to the shared `policy-engine` package. External integrations (payment, identity, notifications) are currently mocked behind interfaces in `app/infrastructure/clients.py` and can be replaced with real providers later.


