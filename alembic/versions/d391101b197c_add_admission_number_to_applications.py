"""add_admission_number_to_applications

Revision ID: d391101b197c
Revises: a86f078e564c
Create Date: 2026-03-17 15:37:44.755083
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd391101b197c'
down_revision: Union[str, None] = 'a86f078e564c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('undergraduate_applications', sa.Column('admission_number', sa.String(length=50), nullable=False))


def downgrade() -> None:
    op.drop_column('undergraduate_applications', 'admission_number')

