from __future__ import annotations

from typing import List

from sqlalchemy.orm import Session

from shared_kernel.exceptions import UnauthorizedError
from shared_kernel.security import PasswordHasher

from ..domain.models import Role, User
from ..infrastructure.repositories import RoleRepository, UserRepository
from ..infrastructure.security import build_access_token_payload


class AuthService:
    def __init__(self, session: Session, hasher: PasswordHasher):
        self._users = UserRepository(session)
        self._roles = RoleRepository(session)
        self._hasher = hasher

    def authenticate(self, username: str, password: str) -> User:
        user = self._users.get_by_username_or_email(username)
        if not user:
            raise UnauthorizedError("Invalid credentials")
        if not self._hasher.verify(password, user.hashed_password):
            raise UnauthorizedError("Invalid credentials")
        if not user.is_active:
            raise UnauthorizedError("User inactive")
        return user

    def build_access_token_payload_for_user(self, user: User) -> dict:
        role_names: List[str] = [role.name for role in (user.roles or [])]
        return build_access_token_payload(user_id=str(user.id), roles=role_names)


class SeedService:
    DEFAULT_ROLES = [
        "system_admin",
        "registrar_staff",
        "department_head",
        "instructor",
        "applicant",
        "student",
    ]

    def __init__(self, session: Session, hasher: PasswordHasher):
        self._session = session
        self._roles = RoleRepository(session)
        self._users = UserRepository(session)
        self._hasher = hasher

    def ensure_default_roles(self) -> None:
        for name in self.DEFAULT_ROLES:
            if self._roles.get_by_name(name) is None:
                role = Role(name=name)
                self._roles.add(role)
        self._session.commit()

    def ensure_admin_user(self, *, username: str, password: str, email: str) -> None:
        existing = self._users.get_by_username_or_email(username)
        if existing:
            return
        from shared_kernel.utils import generate_uuid

        admin = User(
            id=generate_uuid(),
            username=username,
            email=email,
            hashed_password=self._hasher.hash(password),
            is_active=True,
        )

        admin_role = self._roles.get_by_name("system_admin")
        if admin_role:
            admin.roles.append(admin_role)
        self._users.add(admin)
        self._session.commit()

