"""add_enrollment_table_and_enrolled_status

Revision ID: 3b9d2e3f4a5b
Revises: 2a8c1d2e3f4a
Create Date: 2026-03-21 10:10:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '3b9d2e3f4a5b'
down_revision: Union[str, None] = '2a8c1d2e3f4a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add ENROLLED to applicationstatus enum
    op.execute("ALTER TYPE applicationstatus ADD VALUE IF NOT EXISTS 'ENROLLED'")

    # 2. Create enrollments table
    op.create_table(
        'enrollments',
        sa.Column('id', sa.UUID(as_uuid=True), primary_key=True),
        sa.Column('application_id', sa.UUID(as_uuid=True), nullable=False),
        sa.Column('applicant_id', sa.UUID(as_uuid=True), nullable=False),
        sa.Column('university_id', sa.String(length=20), nullable=False),
        sa.Column('portal_password', sa.String(length=255), nullable=False),
        sa.Column('program_id', sa.UUID(as_uuid=True), nullable=True),
        sa.Column('department', sa.String(length=100), nullable=False),
        sa.Column('section', sa.String(length=10), nullable=False),
        sa.Column('enrollment_term', sa.String(length=50), nullable=False),
        sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['application_id'], ['undergraduate_applications.id']),
        sa.ForeignKeyConstraint(['applicant_id'], ['users.id']),
        sa.ForeignKeyConstraint(['program_id'], ['academic_programs.id']),
    )
    op.create_index(op.f('ix_enrollments_university_id'), 'enrollments', ['university_id'], unique=True)
    op.create_index(op.f('ix_enrollments_application_id'), 'enrollments', ['application_id'], unique=True)
    op.create_index(op.f('ix_enrollments_applicant_id'), 'enrollments', ['applicant_id'])


def downgrade() -> None:
    op.drop_index(op.f('ix_enrollments_applicant_id'), table_name='enrollments')
    op.drop_index(op.f('ix_enrollments_application_id'), table_name='enrollments')
    op.drop_index(op.f('ix_enrollments_university_id'), table_name='enrollments')
    op.drop_table('enrollments')
    # Note: Cannot easily remove an enum value in PostgreSQL
