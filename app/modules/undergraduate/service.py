"""
Undergraduate Admission module — business logic.

TODO: Implement UGService with methods:
    - submit_application(applicant_id, data: UGApplicationCreate) -> UGApplication
        - Create application via repository
        - Emit audit log
    - get_application(application_id) -> UGApplication
        - Raise EntityNotFoundError if missing
    - list_applications(skip, limit) -> list[UGApplication]
    - list_my_applications(applicant_id) -> list[UGApplication]
    - update_application(application_id, data, actor_id, actor_role) -> UGApplication
        - This is the human-in-the-loop approval point
        - Update fields, emit audit log with decision
"""
