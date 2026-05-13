"""add sponsorship_type to students

Revision ID: f3a9b1d4c8e2
Revises: e4f5a8c12b3d
Create Date: 2026-05-04 23:00:00.000000

The student's sponsorship type is determined at admission (lives on
UndergraduateApplication.sponsorship_type) and shouldn't be re-asked
on every registration. Denormalising it onto Student lets the
RegistrationService derive it without a cross-module join.

The ``sponsorshiptype`` Postgres ENUM already exists from earlier
admission migrations, so we reuse it with ``create_type=False``.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "f3a9b1d4c8e2"
down_revision: Union[str, None] = "e4f5a8c12b3d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    sponsorship_enum = postgresql.ENUM(
        "GOVERNMENT", "SELF_SPONSORED",
        name="sponsorshiptype",
        create_type=False,  # already created by an earlier admission migration
    )
    op.add_column(
        "students",
        sa.Column("sponsorship_type", sponsorship_enum, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("students", "sponsorship_type")
