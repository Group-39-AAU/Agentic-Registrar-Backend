"""
Undergraduate Admission module — SQLAlchemy ORM models.

TODO: Implement UGApplication model with the following fields:
    - applicant_id (FK -> users.id, UUID, indexed)
    - status (string: PENDING | UNDER_REVIEW | APPROVED | REJECTED)
        → import from app/shared/enums/ once implemented
    - program (string)
    - academic_year (string)
    - remarks (text, nullable)
    - extra_data (JSONB, nullable — for agent evaluations and flexible data)

Inherits from Base (provides id (UUID), created_at, updated_at automatically).
"""

from app.database.base import Base


# class UGApplication(Base):
#     __tablename__ = "ug_applications"
#     ...
