"""merge add_drop_batches with academic_term_phase

Revision ID: 58272130a499
Revises: b2c3d4e5f6a7, f2b3c4d5e609
Create Date: 2026-05-16 15:25:47.052698
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '58272130a499'
down_revision: Union[str, None] = ('b2c3d4e5f6a7', 'f2b3c4d5e609')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
