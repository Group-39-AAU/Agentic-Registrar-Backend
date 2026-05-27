# Auth Module

Handles account creation, login, JWT-based authentication, and password recovery flows for all user roles.

## Responsibilities

- Register new users with role-specific account metadata.
- Authenticate with email or UGR student ID and issue access tokens.
- Enforce first-login password change with `must_change_password`.
- Support forgot/reset password using short-lived JWT reset tokens.

## Key Files

- `router.py`: Auth API endpoints.
- `service.py`: Core auth business logic.
- `models.py`: User/auth ORM models.
- `schemas.py`: Request/response contracts.
- `repository.py`: User persistence helpers.
- `constants.py`: Auth-related constants.

## Endpoints

- `POST /auth/register`
- `POST /auth/login`
- `GET /auth/me`
- `POST /auth/change-password`
- `POST /auth/forgot-password`
- `POST /auth/reset-password`
