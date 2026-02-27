"""
Auth module — SQLAlchemy ORM models.

TODO: Implement User model with the following fields:
    - email (unique, indexed)
    - hashed_password
    - first_name, last_name
    - role (string: STUDENT, REGISTRAR_OFFICER, ADMIN)
        → import from app/shared/enums/ once implemented
    - is_active (boolean)

Inherits from Base (provides id (UUID), created_at, updated_at automatically).
"""

from app.database.base import Base


# class User(Base):
#     __tablename__ = "users"
#     ...
