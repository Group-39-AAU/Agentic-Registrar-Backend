"""widen Section.section_code uniqueness from (term) to (term, dept, sem)

Revision ID: a3c91d4f2e08
Revises: e7b2c5f834a1
Create Date: 2026-05-13 16:50:00.000000

Section codes were globally unique within a term (A, B, … across every
department and semester). The Academic Scheduling Agent now wants
codes to restart at A inside each (department, semester) cohort —
sem-1 of Software Engineering gets sections A, B, C; sem-3 of the
same department also gets A, B, C; codes are still unique within the
cohort but no longer fight for the term-wide alphabet.

Drops ``uq_section_code_per_term`` and replaces it with
``uq_section_code_per_cohort`` on (term_id, department, semester,
section_code).
"""
from typing import Sequence, Union

from alembic import op


revision: str = "a3c91d4f2e08"
down_revision: Union[str, None] = "e7b2c5f834a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_section_code_per_term", "sections", type_="unique",
    )
    op.create_unique_constraint(
        "uq_section_code_per_cohort",
        "sections",
        ["term_id", "department", "semester", "section_code"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_section_code_per_cohort", "sections", type_="unique",
    )
    op.create_unique_constraint(
        "uq_section_code_per_term",
        "sections",
        ["term_id", "section_code"],
    )
