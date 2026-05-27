# Programs Module

Exposes read-only academic program catalog endpoints for applicants and students.

## Responsibilities

- List active academic programs with pagination.
- Filter program list by stream (`NATURAL` or `SOCIAL`).
- Retrieve a single program by ID.

## Key Files

- `router.py`: Program browsing endpoints.
- `models.py`: `AcademicProgram` ORM model.
- `schemas.py`: Program list and detail response schemas.

## Endpoints

- `GET /programs`: List active programs (`stream`, `limit`, `offset` supported).
- `GET /programs/{program_id}`: Get one program detail.
