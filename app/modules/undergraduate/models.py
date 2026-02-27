"""
Undergraduate Admission module — SQLAlchemy ORM models.

TODO: Implement UGApplication model with the following fields:
    - applicant_id (FK -> users.id, indexed)
    - status (string: PENDING | UNDER_REVIEW | APPROVED | REJECTED)
    - program (string)
    - academic_year (string)
    - remarks (text, nullable)
    - extra_data (JSONB, nullable — for agent evaluations and flexible data)

Inherits from Base (provides id, created_at, updated_at automatically).
"""

from app.database.base import Base


# class UGApplication(Base):
#     __tablename__ = "ug_applications"
#     ...
