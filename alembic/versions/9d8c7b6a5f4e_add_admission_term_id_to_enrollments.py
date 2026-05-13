"""add_admission_term_id_to_enrollments

Revision ID: 9d8c7b6a5f4e
Revises: b1c2d3e4f5a6
Create Date: 2026-05-05 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '9d8c7b6a5f4e'
down_revision: Union[str, None] = 'b1c2d3e4f5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'enrollments',
        sa.Column('admission_term_id', sa.UUID(as_uuid=True), nullable=True),
    )

    op.execute(
        """
        UPDATE enrollments AS e
        SET admission_term_id = ua.admission_term_id
        FROM undergraduate_applications AS ua
        WHERE e.application_id = ua.id
        """
    )

    op.alter_column('enrollments', 'admission_term_id', nullable=False)
    op.create_index(
        op.f('ix_enrollments_admission_term_id'),
        'enrollments',
        ['admission_term_id'],
        unique=False,
    )
    op.create_foreign_key(
        'fk_enrollments_admission_term_id_undergraduate_admission_terms',
        'enrollments',
        'undergraduate_admission_terms',
        ['admission_term_id'],
        ['id'],
    )


def downgrade() -> None:
    op.drop_constraint(
        'fk_enrollments_admission_term_id_undergraduate_admission_terms',
        'enrollments',
        type_='foreignkey',
    )
    op.drop_index(op.f('ix_enrollments_admission_term_id'), table_name='enrollments')
    op.drop_column('enrollments', 'admission_term_id')