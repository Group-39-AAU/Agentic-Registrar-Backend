"""
Auth module — SQLAlchemy ORM models.

TODO: Implement User model with the following fields:
    - email (unique, indexed)
    - hashed_password
    - first_name, last_name
    - role (string: STUDENT, REGISTRAR_OFFICER, ADMIN)
    - is_active (boolean)
"""

from app.database.base import Base


# class User(Base):
#     __tablename__ = "users"
#     ...
