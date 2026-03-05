"""
Auth module — Service layer.

Handles user registration (password hashing) and login (JWT issuance).
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.security import create_access_token, hash_password, verify_password
from app.modules.auth.models import User
from app.modules.auth.schemas import RegisterRequest
from app.shared.enums import UserRole

logger = get_logger("auth.service")


class AuthService:
    """Handles registration and authentication."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def register(self, data: RegisterRequest) -> User:
        """
        Create a new student account.
        Raises ValueError if the email is already taken.
        """
        user = User(
            email=data.email,
            hashed_password=hash_password(data.password),
            first_name=data.first_name,
            last_name=data.last_name,
            role=UserRole.STUDENT,
            is_active=True,
        )
        self._db.add(user)

        try:
            await self._db.flush()
        except IntegrityError:
            await self._db.rollback()
            raise ValueError(f"Email {data.email} is already registered")

        await self._db.commit()
        await self._db.refresh(user)
        logger.info("Registered new student: %s", user.email)
        return user

    async def authenticate(self, email: str, password: str) -> str:
        """
        Verify credentials and return a JWT access token.
        Raises ValueError if credentials are invalid.
        """
        result = await self._db.execute(
            select(User).where(
                User.email == email,
                User.is_deleted == False,  # noqa: E712
            )
        )
        user = result.scalar_one_or_none()

        if user is None or not verify_password(password, user.hashed_password):
            raise ValueError("Invalid email or password")

        if not user.is_active:
            raise ValueError("Account is deactivated")

        token = create_access_token(data={"sub": str(user.id), "role": user.role.value})
        logger.info("User authenticated: %s", user.email)
        return token
