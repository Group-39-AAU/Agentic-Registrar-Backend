# Course Management Module

Implements course registration, scheduling, advisory, add/drop, grading integrations, and officer workflows for active students.

## Responsibilities

- Manage registration terms and student registration lifecycle.
- Provide student-facing curriculum, available courses, and registration submission.
- Run department scheduling flows (section allocation and timetable generation).
- Handle add/drop batches with agent review and officer decisions.
- Expose advisory and consultation endpoints (rule-based + LLM-backed flows).
- Support onboarding from admission and instructor assignment workflows.

## Key Files and Subpackages

- `router.py`: Main Course Management API surface.
- `service.py`: Registration, scheduling, advisory, and onboarding orchestration.
- `repository.py`: Data-access helpers for students, registrations, and related entities.
- `models.py` / `schemas.py`: ORM and API contracts for course domain.
- `agents/`: Domain agents (advisory, compliance, scheduling, adjustment).
- `grading/`: Grading and transcript support.
- `records/`: Student records endpoints.
- `standing/`: Academic standing rules and actions.
- `exception_queue/`: Exception handling queues for course operations.

## Main Endpoint Groups

- `GET /courses/terms` and officer term open/close endpoints.
- `GET /courses/me*` student dashboard, curriculum, available courses, registration, and schedule views.
- `POST /courses/officer/sections/allocate` and `POST /courses/officer/schedule/generate`.
- `POST /courses/add-drop/batches` plus officer approve/override/reject queue operations.
- `POST /courses/advisory/*` and officer advisory review endpoints.
- Payment and invoice simulation endpoints under `/courses/registrations/{registration_id}/...`.
- Instructor management endpoints under `/courses/officer/instructors*` and `/courses/officer/instructor-assignments*`.
