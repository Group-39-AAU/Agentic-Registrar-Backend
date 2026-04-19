"""add_uat_records_table

Revision ID: 1e7b9c9d0a5f
Revises: d391101b197c
Create Date: 2026-03-18 09:05:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '1e7b9c9d0a5f'
down_revision: Union[str, None] = 'd391101b197c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add new enum values to applicationstatus
    # Postgres ALTER TYPE ADD VALUE cannot run inside a transaction block in older versions.
    # Alembic's execute usually works if we commit or if using a newer Postgres version, 
    # but to be safe we can use `commit()`.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE applicationstatus ADD VALUE IF NOT EXISTS 'UAT_PENDING';")
        op.execute("ALTER TYPE applicationstatus ADD VALUE IF NOT EXISTS 'UAT_COMPLETED';")

    # 2. Create uat_records table
    op.create_table(
        'uat_records',
        sa.Column('id', sa.UUID(as_uuid=True), primary_key=True),
        sa.Column('uat_id', sa.String(length=20), nullable=False),
        sa.Column('application_id', sa.UUID(as_uuid=True), nullable=False),
        sa.Column('student_name', sa.String(length=255), nullable=False),
        sa.Column('score', sa.Float(), nullable=True),
        sa.Column('is_completed', sa.Boolean(), default=False, nullable=False),
        sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['application_id'], ['undergraduate_applications.id']),
    )
    op.create_index(op.f('ix_uat_records_uat_id'), 'uat_records', ['uat_id'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_uat_records_uat_id'), table_name='uat_records')
    op.drop_table('uat_records')
    # Can't easily drop enum values in Postgres, so we leave them.
