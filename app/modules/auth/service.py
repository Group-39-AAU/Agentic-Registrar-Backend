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
from app.shared.email import EmailService, build_welcome_email
from app.shared.enums import UserRole

logger = get_logger("auth.service")


class AuthService:
    """Handles registration and authentication."""

    def __init__(self, db: AsyncSession, email_service: EmailService | None = None) -> None:
        self._db = db
        self._email_service = email_service

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

        if self._email_service is not None:
            try:
                await self._email_service.send(
                    build_welcome_email(to_email=user.email, first_name=user.first_name)
                )
            except Exception:
                # Registration should still succeed even if email delivery fails.
                logger.exception("Welcome email delivery failed for %s", user.email)

        return user

    async def authenticate(
        self, identifier: str, password: str,
    ) -> tuple[str, bool]:
        """
        Verify credentials and return ``(jwt_token, must_change_password)``.

        ``identifier`` can be either an email (admission flow) or a UGR
        student ID like ``UGR/0001/14`` (post-enrollment portal flow).
        Lookup tries email first; if no user is found, falls back to
        joining ``students`` on student_id → user_id. The shared User
        identity backs both paths so audit history stays unified.

        Raises ValueError on invalid credentials or deactivated account.
        """
        user = await self._lookup_user_by_identifier(identifier)
        if user is None or not verify_password(password, user.hashed_password):
            raise ValueError("Invalid credentials")

        if not user.is_active:
            raise ValueError("Account is deactivated")

        token = create_access_token(
            data={"sub": str(user.id), "role": user.role.value},
        )
        logger.info(
            "User authenticated: %s (must_change_password=%s)",
            user.email, user.must_change_password,
        )
        return token, user.must_change_password

    async def _lookup_user_by_identifier(self, identifier: str) -> User | None:
        """
        Resolve email-or-student-id to a User. Email match wins; if
        nothing matches, try the Student.student_id → user_id join.
        """
        by_email = (
            await self._db.execute(
                select(User).where(
                    User.email == identifier,
                    User.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()
        if by_email is not None:
            return by_email

        # Defer the import: course module shouldn't be required to
        # boot the auth module in environments where it's stubbed out.
        from app.modules.course.models import Student
        by_student_id = (
            await self._db.execute(
                select(User)
                .join(Student, Student.user_id == User.id)
                .where(
                    Student.student_id == identifier,
                    User.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()
        return by_student_id

    async def change_password(
        self,
        user: User,
        current_password: str,
        new_password: str,
    ) -> None:
        """
        Replace the user's password after verifying the current one.
        Clears ``must_change_password`` so the lockout middleware lets
        every other endpoint through again.
        """
        if not verify_password(current_password, user.hashed_password):
            raise ValueError("Current password is incorrect")
        if current_password == new_password:
            raise ValueError("New password must differ from current password")

        user.hashed_password = hash_password(new_password)
        user.must_change_password = False
        await self._db.commit()
        await self._db.refresh(user)
        logger.info("Password changed for user %s", user.email)
