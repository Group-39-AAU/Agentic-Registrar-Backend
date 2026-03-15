# API Gateway Service

FastAPI-based API gateway for the Agentic Registrar system.

## Responsibilities

- Front all public/backend-facing APIs.
- Route and proxy to internal services (identity, admissions, course management, documents, notifications).
- Validate JWTs via the identity service and enforce authentication where needed.
- Propagate correlation IDs across service boundaries.

## Route groups

- `/auth/*` → identity-service
- `/undergrad/*` → undergrad-admission-service
- `/graduate/*` → graduate-admission-service
- `/courses/*` → course-management-service
- `/documents/*` → document-service
- `/notifications/*` → notification-service

## Endpoints

- `GET /health` – gateway health.
- Proxy endpoints under the route groups above, using `httpx` for upstream calls.

## Configuration

Downstream service URLs are configured via environment variables (with sensible defaults for Docker compose):

- `IDENTITY_SERVICE_URL`
- `UNDERGRAD_ADMISSION_SERVICE_URL`
- `GRADUATE_ADMISSION_SERVICE_URL`
- `COURSE_MANAGEMENT_SERVICE_URL`
- `DOCUMENT_SERVICE_URL`
- `NOTIFICATION_SERVICE_URL`

## Middleware and behavior

- The gateway uses `CorrelationIdMiddleware` from `shared-kernel` to manage an `X-Request-ID` header across requests.
- JWTs are validated by calling the identity-service `/auth/token/introspect` endpoint.
- Proxy routes pass through most headers and JSON bodies, adding `X-Request-ID` when available.


