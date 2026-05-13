"""add_term_and_run_to_ranking_results

Revision ID: b1c2d3e4f5a6
Revises: b4d8e2f1a906
Create Date: 2026-05-05 10:00:00.000000

Re-parented from 9f2d4c8a1b7e (the original branch point) onto
b4d8e2f1a906 so the alembic history is a single linear chain
rather than two parallel heads. The ranking + admission-term-id
migrations only touch ranking_results / enrollments and are
independent of the course-management chain, so re-parenting is
behaviour-preserving for upgrade-from-base flows.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "b4d8e2f1a906"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ranking_results",
        sa.Column("admission_term_id", sa.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "ranking_results",
        sa.Column("ranking_run_number", sa.Integer(), nullable=False, server_default="1"),
    )

    op.create_foreign_key(
        "fk_ranking_results_admission_term_id",
        "ranking_results",
        "undergraduate_admission_terms",
        ["admission_term_id"],
        ["id"],
    )
    op.create_index(
        op.f("ix_ranking_results_admission_term_id"),
        "ranking_results",
        ["admission_term_id"],
        unique=False,
    )

    op.execute(
        """
        UPDATE ranking_results rr
        SET admission_term_id = ua.admission_term_id
        FROM undergraduate_applications ua
        WHERE rr.application_id = ua.id
        """
    )

    op.alter_column("ranking_results", "admission_term_id", nullable=False)
    op.alter_column("ranking_results", "ranking_run_number", server_default=None)


def downgrade() -> None:
    op.drop_index(op.f("ix_ranking_results_admission_term_id"), table_name="ranking_results")
    op.drop_constraint("fk_ranking_results_admission_term_id", "ranking_results", type_="foreignkey")
    op.drop_column("ranking_results", "ranking_run_number")
    op.drop_column("ranking_results", "admission_term_id")
