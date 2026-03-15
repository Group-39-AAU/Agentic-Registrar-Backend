from __future__ import annotations

from typing import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from shared_kernel.utils import generate_uuid

from ..domain.models import Role, User


class UserRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_username_or_email(self, identifier: str) -> User | None:
        stmt = select(User).where((User.username == identifier) | (User.email == identifier))
        return self._session.execute(stmt).scalars().first()

    def add(self, user: User) -> None:
        if user.id is None:  # type: ignore[truthy-function]
            user.id = generate_uuid()  # type: ignore[assignment]
        self._session.add(user)


class RoleRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_name(self, name: str) -> Role | None:
        stmt = select(Role).where(Role.name == name)
        return self._session.execute(stmt).scalars().first()

    def list_by_names(self, names: Iterable[str]) -> Sequence[Role]:
        stmt = select(Role).where(Role.name.in_(list(names)))
        return list(self._session.execute(stmt).scalars().all())

    def add(self, role: Role) -> None:
        if role.id is None:  # type: ignore[truthy-function]
            role.id = generate_uuid()  # type: ignore[assignment]
        self._session.add(role)

