"""add academic_standings + academic_standing_history

Track C PR C1 — Senate-aligned Academic Standing scaffolding.

Creates the ``academicstatustype`` Postgres ENUM (used by both new
tables for proposed_status / final_status / prior_status columns)
and the two new tables:

  ``academic_standings``        — one row per (student, term) carrying
                                  the SGPA/CGPA math, the Article-91
                                  context flags, and the DH
                                  authorisation fields.

  ``academic_standing_history`` — append-only audit trail of every
                                  state transition (PROPOSED →
                                  AUTHORISED / OVERRIDDEN / RE_COMPUTED).

This PR ships *only* the schema + read endpoints. The compute /
authorise / override write paths land in PR C2.

Revision ID: d8f2a3b4e605
Revises: c5e9a1b3d702
Create Date: 2026-05-17 18:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d8f2a3b4e605"
down_revision: Union[str, None] = "c5e9a1b3d702"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ACADEMIC_STATUS = (
    "PROMOTED", "WARNING", "DISTINCTION", "DISMISSED", "INCOMPLETE",
)
_STANDING_EVENTS = ("PROPOSED", "AUTHORISED", "OVERRIDDEN", "RE_COMPUTED")


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Postgres ENUM for academic-standing values. ``create_type=False``
    # combined with the explicit ``create`` call mirrors the pattern
    # used by the existing ``gradeletter`` migration.
    academic_status = postgresql.ENUM(
        *_ACADEMIC_STATUS, name="academicstatustype", create_type=False,
    )
    academic_status.create(bind, checkfirst=True)

    # 2. academic_standings — one per (student, term).
    op.create_table(
        "academic_standings",
        sa.Column("student_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("term_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("department", sa.String(100), nullable=False),
        sa.Column("sgpa", sa.Float(), nullable=True),
        sa.Column("cgpa", sa.Float(), nullable=True),
        sa.Column(
            "term_credit_hours", sa.Integer(),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "cumulative_credit_hours", sa.Integer(),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "f_count_term", sa.Integer(),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "f_credit_total_term", sa.Integer(),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "is_first_semester", sa.Boolean(),
            nullable=False, server_default=sa.text("false"),
        ),
        sa.Column(
            "is_first_year", sa.Boolean(),
            nullable=False, server_default=sa.text("false"),
        ),
        sa.Column("prior_status", academic_status, nullable=True),
        sa.Column(
            "consecutive_warning_count", sa.Integer(),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column("proposed_status", academic_status, nullable=False),
        sa.Column("final_status", academic_status, nullable=True),
        sa.Column(
            "requires_review", sa.Boolean(),
            nullable=False, server_default=sa.text("false"),
        ),
        sa.Column(
            "computed_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False, server_default=sa.text("now()"),
        ),
        sa.Column("computed_by_agent_id", sa.String(120), nullable=True),
        sa.Column("authorised_by_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "authorised_at",
            postgresql.TIMESTAMP(timezone=True), nullable=True,
        ),
        sa.Column("override_reason", sa.Text(), nullable=True),
        sa.Column(
            "is_deleted", sa.Boolean(),
            nullable=False, server_default=sa.text("false"),
        ),
        sa.Column(
            "id", sa.UUID(as_uuid=True),
            primary_key=True, nullable=False,
        ),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"]),
        sa.ForeignKeyConstraint(["term_id"], ["academic_terms.id"]),
        sa.ForeignKeyConstraint(["authorised_by_id"], ["users.id"]),
        sa.UniqueConstraint(
            "student_id", "term_id",
            name="uq_standing_per_student_term",
        ),
        sa.CheckConstraint(
            "sgpa IS NULL OR (sgpa >= 0 AND sgpa <= 4.0)",
            name="ck_standing_sgpa_range",
        ),
        sa.CheckConstraint(
            "cgpa IS NULL OR (cgpa >= 0 AND cgpa <= 4.0)",
            name="ck_standing_cgpa_range",
        ),
        sa.CheckConstraint(
            "term_credit_hours >= 0",
            name="ck_standing_term_credit_hours_non_negative",
        ),
        sa.CheckConstraint(
            "cumulative_credit_hours >= 0",
            name="ck_standing_cumulative_credit_hours_non_negative",
        ),
        sa.CheckConstraint(
            "f_count_term >= 0",
            name="ck_standing_f_count_non_negative",
        ),
        sa.CheckConstraint(
            "consecutive_warning_count >= 0",
            name="ck_standing_consecutive_warning_non_negative",
        ),
    )
    op.create_index(
        op.f("ix_academic_standings_student_id"),
        "academic_standings", ["student_id"], unique=False,
    )
    op.create_index(
        op.f("ix_academic_standings_term_id"),
        "academic_standings", ["term_id"], unique=False,
    )
    op.create_index(
        op.f("ix_academic_standings_department"),
        "academic_standings", ["department"], unique=False,
    )
    op.create_index(
        op.f("ix_academic_standings_proposed_status"),
        "academic_standings", ["proposed_status"], unique=False,
    )
    op.create_index(
        op.f("ix_academic_standings_final_status"),
        "academic_standings", ["final_status"], unique=False,
    )
    op.create_index(
        op.f("ix_academic_standings_requires_review"),
        "academic_standings", ["requires_review"], unique=False,
    )

    # 3. academic_standing_history — append-only audit trail.
    op.create_table(
        "academic_standing_history",
        sa.Column("standing_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("event", sa.String(20), nullable=False),
        sa.Column("previous_status", academic_status, nullable=True),
        sa.Column("new_status", academic_status, nullable=False),
        sa.Column("changed_by_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_id", sa.String(120), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "id", sa.UUID(as_uuid=True),
            primary_key=True, nullable=False,
        ),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["standing_id"], ["academic_standings.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["changed_by_id"], ["users.id"]),
        sa.CheckConstraint(
            "event IN ('"
            + "', '".join(_STANDING_EVENTS)
            + "')",
            name="ck_standing_history_event",
        ),
    )
    op.create_index(
        op.f("ix_academic_standing_history_standing_id"),
        "academic_standing_history", ["standing_id"], unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_academic_standing_history_standing_id"),
        table_name="academic_standing_history",
    )
    op.drop_table("academic_standing_history")

    for ix in (
        "ix_academic_standings_requires_review",
        "ix_academic_standings_final_status",
        "ix_academic_standings_proposed_status",
        "ix_academic_standings_department",
        "ix_academic_standings_term_id",
        "ix_academic_standings_student_id",
    ):
        op.drop_index(op.f(ix), table_name="academic_standings")
    op.drop_table("academic_standings")

    bind = op.get_bind()
    postgresql.ENUM(name="academicstatustype").drop(bind, checkfirst=True)
