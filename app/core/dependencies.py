"""
Reusable FastAPI dependencies.

TODO: Implement:
    - DBSession type alias (Annotated[AsyncSession, Depends(get_db)])
    - get_current_user(token, db) -> User
        - Decode JWT, look up user via AuthRepository
        - Raise 401 if invalid
    - require_role(*allowed_roles) -> dependency factory
        - Returns a dependency that checks current_user.role
        - Raises 403 if not in allowed_roles

Usage in routes:
    @router.get("/admin", dependencies=[Depends(require_role("ADMIN"))])
"""

from typing import Annotated

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.database.session import get_db

# OAuth2 scheme for token extraction from Authorization header
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

# Type aliases for cleaner route signatures
DBSession = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]

# Implement get_current_user and require_role here
