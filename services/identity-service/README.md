# Identity Service

FastAPI-based identity service for the Agentic Registrar system.

## Responsibilities

- User accounts, roles, and basic RBAC.
- Authentication via username/password and JWT.
- JWT issuance, validation, and token introspection for other services.

## Environment variables

The service reads the following variables (see root `.env.example`):

- **Core**
  - `ENVIRONMENT` – `local|dev|staging|prod`
- **Database**
  - `POSTGRES_HOST`
  - `POSTGRES_PORT`
  - `POSTGRES_DB`
  - `POSTGRES_USER`
  - `POSTGRES_PASSWORD`
- **JWT**
  - `JWT_SECRET`
  - `JWT_ALGORITHM`
  - `JWT_ACCESS_TOKEN_EXPIRES_MINUTES`
- **Bootstrap admin (optional)**
  - `IDENTITY_ADMIN_USERNAME`
  - `IDENTITY_ADMIN_PASSWORD`
  - `IDENTITY_ADMIN_EMAIL`

## Endpoints

- `GET /health` – health check.
- `POST /auth/login` – login with `{"username": "...", "password": "..."}` and receive a bearer token.
- `POST /auth/token/introspect` – validate a JWT and return subject, roles, and `active` flag.
- `GET /auth/me` – return information about the current user based on the bearer token.

## Running locally

From the monorepo root:

```bash
make run-identity
```

The service will be available at `http://localhost:8001`.


