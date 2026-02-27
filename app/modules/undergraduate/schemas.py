"""
Undergraduate Admission module — Pydantic schemas.

TODO: Implement the following schemas:
    - UGApplicationCreate
        - program (str, required)
        - academic_year (str, required)
        - extra_data (dict, optional)
    - UGApplicationUpdate
        - status (str, optional — PENDING|UNDER_REVIEW|APPROVED|REJECTED)
        - remarks (str, optional)
        - extra_data (dict, optional)
    - UGApplicationResponse
        - id, applicant_id, status, program, academic_year
        - remarks, extra_data, created_at, updated_at
        - model_config = {"from_attributes": True}
"""
