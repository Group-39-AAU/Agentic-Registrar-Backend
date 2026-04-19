"""add_undergraduate_admission_terms

Revision ID: 7c1a2b3d4e5f
Revises: 3b9d2e3f4a5b
Create Date: 2026-04-07 12:00:00.000000
"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "7c1a2b3d4e5f"
down_revision: Union[str, None] = "3b9d2e3f4a5b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "undergraduate_admission_terms",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("term_name", sa.String(length=100), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("is_open", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index(op.f("ix_undergraduate_admission_terms_term_name"), "undergraduate_admission_terms", ["term_name"], unique=True)

    op.add_column("undergraduate_applications", sa.Column("admission_term_id", sa.UUID(as_uuid=True), nullable=True))

    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT DISTINCT admission_term FROM undergraduate_applications")).fetchall()
    for row in rows:
        bind.execute(
            sa.text(
                """
                INSERT INTO undergraduate_admission_terms (id, term_name, start_date, end_date, is_open, is_deleted)
                VALUES (:id, :term_name, CURRENT_DATE, CURRENT_DATE + INTERVAL '120 days', true, false)
                """
            ),
            {"id": str(uuid.uuid4()), "term_name": row[0]},
        )

    op.execute(
        """
        UPDATE undergraduate_applications a
        SET admission_term_id = t.id
        FROM undergraduate_admission_terms t
        WHERE t.term_name = a.admission_term
        """
    )

    op.alter_column("undergraduate_applications", "admission_term_id", nullable=False)
    op.create_foreign_key(
        "fk_undergraduate_applications_admission_term_id",
        "undergraduate_applications",
        "undergraduate_admission_terms",
        ["admission_term_id"],
        ["id"],
    )
    op.create_index(
        op.f("ix_undergraduate_applications_admission_term_id"),
        "undergraduate_applications",
        ["admission_term_id"],
        unique=False,
    )

    op.drop_constraint("uq_one_app_per_term", "undergraduate_applications", type_="unique")
    op.create_unique_constraint(
        "uq_one_app_per_term",
        "undergraduate_applications",
        ["applicant_id", "admission_term_id"],
    )
    op.drop_index(op.f("ix_undergraduate_applications_admission_term"), table_name="undergraduate_applications")
    op.drop_column("undergraduate_applications", "admission_term")

    op.add_column("stream_quotas", sa.Column("admission_term_id", sa.UUID(as_uuid=True), nullable=True))
    op.execute(
        """
        UPDATE stream_quotas sq
        SET admission_term_id = t.id
        FROM undergraduate_admission_terms t
        WHERE t.term_name = sq.admission_term
        """
    )
    op.alter_column("stream_quotas", "admission_term_id", nullable=False)
    op.create_foreign_key(
        "fk_stream_quotas_admission_term_id",
        "stream_quotas",
        "undergraduate_admission_terms",
        ["admission_term_id"],
        ["id"],
    )
    op.create_index(op.f("ix_stream_quotas_admission_term_id"), "stream_quotas", ["admission_term_id"], unique=False)

    op.drop_index(op.f("ix_stream_quotas_stream"), table_name="stream_quotas")
    op.create_index(op.f("ix_stream_quotas_stream"), "stream_quotas", ["stream"], unique=False)
    op.create_unique_constraint("uq_stream_quotas_stream_term", "stream_quotas", ["stream", "admission_term_id"])
    op.drop_column("stream_quotas", "admission_term")


def downgrade() -> None:
    op.add_column("stream_quotas", sa.Column("admission_term", sa.String(length=50), nullable=True))
    op.execute(
        """
        UPDATE stream_quotas sq
        SET admission_term = t.term_name
        FROM undergraduate_admission_terms t
        WHERE t.id = sq.admission_term_id
        """
    )
    op.alter_column("stream_quotas", "admission_term", nullable=False)
    op.drop_constraint("uq_stream_quotas_stream_term", "stream_quotas", type_="unique")
    op.drop_constraint("fk_stream_quotas_admission_term_id", "stream_quotas", type_="foreignkey")
    op.drop_index(op.f("ix_stream_quotas_admission_term_id"), table_name="stream_quotas")
    op.drop_column("stream_quotas", "admission_term_id")
    op.drop_index(op.f("ix_stream_quotas_stream"), table_name="stream_quotas")
    op.create_index(op.f("ix_stream_quotas_stream"), "stream_quotas", ["stream"], unique=True)

    op.add_column("undergraduate_applications", sa.Column("admission_term", sa.String(length=50), nullable=True))
    op.execute(
        """
        UPDATE undergraduate_applications a
        SET admission_term = t.term_name
        FROM undergraduate_admission_terms t
        WHERE t.id = a.admission_term_id
        """
    )
    op.alter_column("undergraduate_applications", "admission_term", nullable=False)

    op.drop_constraint("uq_one_app_per_term", "undergraduate_applications", type_="unique")
    op.create_unique_constraint("uq_one_app_per_term", "undergraduate_applications", ["applicant_id", "admission_term"])

    op.drop_constraint("fk_undergraduate_applications_admission_term_id", "undergraduate_applications", type_="foreignkey")
    op.drop_index(op.f("ix_undergraduate_applications_admission_term_id"), table_name="undergraduate_applications")
    op.drop_column("undergraduate_applications", "admission_term_id")
    op.create_index(op.f("ix_undergraduate_applications_admission_term"), "undergraduate_applications", ["admission_term"], unique=False)

    op.drop_index(op.f("ix_undergraduate_admission_terms_term_name"), table_name="undergraduate_admission_terms")
    op.drop_table("undergraduate_admission_terms")
