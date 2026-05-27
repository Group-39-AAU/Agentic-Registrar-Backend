# MoE Module

Provides read-only access to Ministry of Education (MoE) student records used by admission verification flows.

## Responsibilities

- Expose official MoE matric data by admission number.
- Serve as the source of truth for credential verification agents.

## Key Files

- `router.py`: MoE lookup API endpoint.
- `models.py`: `MoeStudentRecord` ORM model.
- `schemas.py`: Response contracts for MoE data.

## Endpoints

- `GET /moe/records/{admission_number}`: Fetch a student's MoE record.
