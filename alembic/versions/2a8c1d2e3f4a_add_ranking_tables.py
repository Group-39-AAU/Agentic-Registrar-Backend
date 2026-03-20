"""add_ranking_tables

Revision ID: 2a8c1d2e3f4a
Revises: 1e7b9c9d0a5f
Create Date: 2026-03-20 15:40:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '2a8c1d2e3f4a'
down_revision: Union[str, None] = '1e7b9c9d0a5f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create stream_quotas table
    op.create_table(
        'stream_quotas',
        sa.Column('id', sa.UUID(as_uuid=True), primary_key=True),
        sa.Column('stream', postgresql.ENUM('NATURAL', 'SOCIAL', name='streamtype', create_type=False), nullable=False),
        sa.Column('max_capacity', sa.Integer(), nullable=False),
        sa.Column('admission_term', sa.String(length=50), nullable=False),
        sa.Column('is_deleted', sa.Boolean(), default=False, nullable=False),
        sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index(op.f('ix_stream_quotas_stream'), 'stream_quotas', ['stream'], unique=True)

    # 2. Create ranking_results table
    op.create_table(
        'ranking_results',
        sa.Column('id', sa.UUID(as_uuid=True), primary_key=True),
        sa.Column('ranking_batch_id', sa.String(length=50), nullable=False),
        sa.Column('application_id', sa.UUID(as_uuid=True), nullable=False),
        sa.Column('grade12_score', sa.Float(), nullable=False),
        sa.Column('uat_score', sa.Float(), nullable=False),
        sa.Column('final_score', sa.Float(), nullable=False),
        sa.Column('category', sa.String(length=20), nullable=False),
        sa.Column('rank_position', sa.Integer(), nullable=False),
        sa.Column('assigned_program_id', sa.UUID(as_uuid=True), nullable=True),
        sa.Column('assigned_stream', postgresql.ENUM('NATURAL', 'SOCIAL', name='streamtype', create_type=False), nullable=True),
        sa.Column('is_assigned', sa.Boolean(), default=False, nullable=False),
        sa.Column('assignment_detail', sa.String(length=255), nullable=True),
        sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['application_id'], ['undergraduate_applications.id']),
        sa.ForeignKeyConstraint(['assigned_program_id'], ['academic_programs.id']),
    )
    op.create_index(op.f('ix_ranking_results_batch_id'), 'ranking_results', ['ranking_batch_id'])


def downgrade() -> None:
    op.drop_index(op.f('ix_ranking_results_batch_id'), table_name='ranking_results')
    op.drop_table('ranking_results')
    op.drop_index(op.f('ix_stream_quotas_stream'), table_name='stream_quotas')
    op.drop_table('stream_quotas')
