"""add must_change_password to users

Revision ID: d2e8f6a1c4b9
Revises: 9f2d4c8a1b7e
Create Date: 2026-05-04 12:00:00.000000

Backs the portal-credential lifecycle: when an officer onboards a
student via OnboardingService, the User row's password is replaced by
a hashed 4-digit PIN and this flag is set True. The /auth/change-password
endpoint clears the flag; until it does, every other endpoint 403s.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "d2e8f6a1c4b9"
down_revision: Union[str, None] = "9f2d4c8a1b7e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "must_change_password")
