"""add add/drop batches workflow

Introduces the batch model for add/drop requests:

  - new table ``add_drop_batches`` carrying the workflow status
    (PENDING_AGENT → AGENT_APPROVED|AGENT_DENIED → APPLIED|REJECTED
     or CANCELLED), the per-batch agent verdict payload, and the
    officer decision audit trail.
  - new column ``add_drop_requests.batch_id`` (nullable FK) so each
    item links back to its batch. Pre-existing items have NULL
    batch_id; the new flow always sets it.

Also creates the ``adddropbatchstatus`` enum type which the new
column references.

Revision ID: f2b3c4d5e609
Revises: e1a2b3c4d508
Create Date: 2026-05-16 09:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f2b3c4d5e609"
down_revision: Union[str, None] = "e1a2b3c4d508"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ADD_DROP_BATCH_STATUS = (
    "PENDING_AGENT", "AGENT_APPROVED", "AGENT_DENIED",
    "APPLIED", "REJECTED", "CANCELLED",
)


def upgrade() -> None:
    bind = op.get_bind()

    batch_status = postgresql.ENUM(
        *_ADD_DROP_BATCH_STATUS,
        name="adddropbatchstatus", create_type=False,
    )
    batch_status.create(bind, checkfirst=True)

    op.create_table(
        "add_drop_batches",
        sa.Column("registration_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("student_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status", batch_status,
            nullable=False, server_default="PENDING_AGENT",
        ),
        sa.Column(
            "agent_reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False, server_default="[]",
        ),
        sa.Column("officer_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "officer_decision_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column("officer_justification", sa.Text(), nullable=True),
        sa.Column(
            "is_deleted", sa.Boolean(),
            nullable=False, server_default=sa.text("false"),
        ),
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.ForeignKeyConstraint(["registration_id"], ["registrations.id"]),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"]),
        sa.ForeignKeyConstraint(["officer_id"], ["users.id"]),
    )
    op.create_index(
        op.f("ix_add_drop_batches_registration_id"),
        "add_drop_batches", ["registration_id"], unique=False,
    )
    op.create_index(
        op.f("ix_add_drop_batches_student_id"),
        "add_drop_batches", ["student_id"], unique=False,
    )
    op.create_index(
        op.f("ix_add_drop_batches_status"),
        "add_drop_batches", ["status"], unique=False,
    )

    op.add_column(
        "add_drop_requests",
        sa.Column("batch_id", sa.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_add_drop_requests_batch_id",
        "add_drop_requests", "add_drop_batches",
        ["batch_id"], ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        op.f("ix_add_drop_requests_batch_id"),
        "add_drop_requests", ["batch_id"], unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_add_drop_requests_batch_id"),
        table_name="add_drop_requests",
    )
    op.drop_constraint(
        "fk_add_drop_requests_batch_id",
        "add_drop_requests",
        type_="foreignkey",
    )
    op.drop_column("add_drop_requests", "batch_id")

    for index_name in (
        "ix_add_drop_batches_status",
        "ix_add_drop_batches_student_id",
        "ix_add_drop_batches_registration_id",
    ):
        op.drop_index(op.f(index_name), table_name="add_drop_batches")
    op.drop_table("add_drop_batches")

    bind = op.get_bind()
    postgresql.ENUM(name="adddropbatchstatus").drop(bind, checkfirst=True)
