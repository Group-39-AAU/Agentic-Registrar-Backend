# Testing Center Module

Simulates a physical UAT testing center that reports exam results back to the registrar backend.

## Responsibilities

- Complete UAT sessions and assign simulated scores.
- Publish `UATCompletedEvent` for downstream admission workflow transitions.
- Provide browser-friendly UAT simulation page for email-driven flows.
- Expose lookup endpoint for stored UAT records.

## Key Files

- `router.py`: UAT callback, session page, and lookup endpoints.
- `models.py`: `UATRecord` ORM model.
- `schemas.py`: Callback and record response schemas.

## Endpoints

- `POST /testing-center/callback/{uat_id}`: Complete UAT and persist score.
- `GET /testing-center/uat-session/{uat_id}`: Render simulation page that submits callback.
- `GET /testing-center/records/{uat_id}`: Retrieve UAT record.
