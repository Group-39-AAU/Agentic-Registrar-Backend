"""add classrooms table

Revision ID: e7b2c5f834a1
Revises: d4f7e1c92a85
Create Date: 2026-05-07 22:30:00.000000

Step 1 of the room-as-entity rework. Right now the
AcademicSchedulingAgent picks rooms from a hard-coded in-memory list
(``DEFAULT_ROOM_INVENTORY``) and writes the chosen name onto
``Section.room`` as a free-form string. We're moving rooms into the
database so the registrar can manage them per-department.

This migration only adds the ``classrooms`` table. The
``Section.room`` column stays as-is for now — wiring it to a FK
(or replacing the agent's room source) is a follow-up commit.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "e7b2c5f834a1"
down_revision: Union[str, None] = "d4f7e1c92a85"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "classrooms",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("department", sa.String(length=100), nullable=False),
        sa.Column(
            "is_deleted", sa.Boolean(), nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.UniqueConstraint("name", name="uq_classrooms_name"),
        sa.CheckConstraint(
            "capacity > 0", name="ck_classroom_capacity_positive",
        ),
    )
    op.create_index("ix_classrooms_name", "classrooms", ["name"])
    op.create_index(
        "ix_classrooms_department", "classrooms", ["department"],
    )


def downgrade() -> None:
    op.drop_index("ix_classrooms_department", table_name="classrooms")
    op.drop_index("ix_classrooms_name", table_name="classrooms")
    op.drop_table("classrooms")
