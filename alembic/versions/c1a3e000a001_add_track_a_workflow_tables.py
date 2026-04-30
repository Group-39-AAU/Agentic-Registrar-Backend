"""add track a workflow tables

Track A workflow schema layered on top of the Phase-0 catalog
(c0a3e0b00001):

  Tables (FK-dependency order):
    registrations
    registration_courses
    registration_status_history
    add_drop_requests
    advisory_recommendations
    prerequisite_overrides
    schedule_conflicts

  Enums (only the new ones; Phase-0 pre-created the rest):
    adddroprequeststatus
    scheduleconflicttype
    scheduleconflictstatus

The migration creates each table with FK targets that already exist
in c0a3e0b00001 (academic_terms, courses, sections, students,
instructors, course_offerings, course_management_officers) plus the
shared ``users`` table created in 5e3743110c60.

Revision ID: c1a3e000a001
Revises: c0a3e0b00001
Create Date: 2026-04-30 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "c1a3e000a001"
down_revision: Union[str, None] = "c0a3e0b00001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ── Enum value lists (must stay in sync with app.shared.enums) ───
# Existing-from-Phase-0 (referenced via create_type=False):

_REGISTRATION_STATUS = (
    "REGISTRATION_OPEN", "ADVISOR_REVIEW", "CHECKING_PREREQUISITES",
    "CHECKING_PAYMENT", "PAYMENT_HOLD", "VALIDATION_SUCCESS",
    "REGISTERED", "ADD_DROP_WINDOW", "CANCELLED",
)
_ADD_DROP_ACTION = ("ADD", "DROP")
_RISK_STATUS = ("LOW", "MEDIUM", "HIGH")
_SPONSORSHIP_TYPE = ("GOVERNMENT", "SELF_SPONSORED")  # from phase2_schema

# New-in-Track-A:

_ADD_DROP_REQUEST_STATUS = (
    "PENDING", "APPROVED", "DENIED", "OVERRIDDEN", "APPLIED",
)
_SCHEDULE_CONFLICT_TYPE = (
    "ROOM_DOUBLE_BOOKED", "INSTRUCTOR_DOUBLE_BOOKED",
    "NO_AVAILABLE_ROOM", "NO_AVAILABLE_INSTRUCTOR",
)
_SCHEDULE_CONFLICT_STATUS = (
    "OPEN", "RESOLVED_BY_AGENT", "RESOLVED_BY_OFFICER", "DISMISSED",
)


def upgrade() -> None:
    bind = op.get_bind()

    # ── 1. Define each ENUM type ONCE as a postgresql.ENUM instance ──
    # We use postgresql.ENUM (not sa.Enum) so create_type=False is
    # preserved through SQLAlchemy's adapt_emulated_to_native — sa.Enum
    # drops the flag during adaptation, which causes duplicate-CREATE-TYPE
    # errors at op.create_table time. We also reuse the same instance
    # across multiple columns so SQLAlchemy's _on_table_create memo
    # short-circuits the second-and-later CREATEs.
    registration_status = postgresql.ENUM(
        *_REGISTRATION_STATUS, name="registrationstatus", create_type=False,
    )
    add_drop_action = postgresql.ENUM(
        *_ADD_DROP_ACTION, name="adddropaction", create_type=False,
    )
    risk_status = postgresql.ENUM(
        *_RISK_STATUS, name="riskstatus", create_type=False,
    )
    sponsorship_type = postgresql.ENUM(
        *_SPONSORSHIP_TYPE, name="sponsorshiptype", create_type=False,
    )
    add_drop_request_status = postgresql.ENUM(
        *_ADD_DROP_REQUEST_STATUS, name="adddroprequeststatus", create_type=False,
    )
    schedule_conflict_type = postgresql.ENUM(
        *_SCHEDULE_CONFLICT_TYPE, name="scheduleconflicttype", create_type=False,
    )
    schedule_conflict_status = postgresql.ENUM(
        *_SCHEDULE_CONFLICT_STATUS, name="scheduleconflictstatus", create_type=False,
    )

    # Pre-create the enums Track A introduces. Pre-existing types from
    # earlier migrations (registrationstatus, adddropaction, riskstatus,
    # sponsorshiptype) are NOT pre-created here — they were either created
    # by phase2_schema (sponsorshiptype) or, after this migration drop
    # of Phase 0's pre-creation, do not yet exist. checkfirst=True keeps
    # the calls idempotent regardless.
    for enum_obj in (
        registration_status,
        add_drop_action,
        risk_status,
        add_drop_request_status,
        schedule_conflict_type,
        schedule_conflict_status,
    ):
        enum_obj.create(bind, checkfirst=True)

    # ── 2. registrations ─────────────────────────────────────────
    op.create_table(
        "registrations",
        sa.Column("student_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("term_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status", registration_status,
            nullable=False, server_default="REGISTRATION_OPEN",
        ),
        sa.Column(
            "sponsorship_type", sponsorship_type, nullable=False,
        ),
        sa.Column("payment_reference", sa.String(length=255), nullable=True),
        sa.Column("finalised_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"]),
        sa.ForeignKeyConstraint(["term_id"], ["academic_terms.id"]),
        sa.UniqueConstraint(
            "student_id", "term_id",
            name="uq_one_registration_per_student_term",
        ),
    )
    op.create_index(op.f("ix_registrations_student_id"), "registrations", ["student_id"], unique=False)
    op.create_index(op.f("ix_registrations_term_id"), "registrations", ["term_id"], unique=False)
    op.create_index(op.f("ix_registrations_status"), "registrations", ["status"], unique=False)

    # ── 3. registration_courses ──────────────────────────────────
    op.create_table(
        "registration_courses",
        sa.Column("registration_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("section_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("is_dropped", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["registration_id"], ["registrations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"]),
        sa.ForeignKeyConstraint(["section_id"], ["sections.id"]),
        sa.UniqueConstraint(
            "registration_id", "course_id",
            name="uq_registration_course_pair",
        ),
    )
    op.create_index(op.f("ix_registration_courses_registration_id"), "registration_courses", ["registration_id"], unique=False)
    op.create_index(op.f("ix_registration_courses_course_id"), "registration_courses", ["course_id"], unique=False)
    op.create_index(op.f("ix_registration_courses_section_id"), "registration_courses", ["section_id"], unique=False)

    # ── 4. registration_status_history ───────────────────────────
    op.create_table(
        "registration_status_history",
        sa.Column("registration_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("previous_status", registration_status, nullable=True),
        sa.Column("new_status", registration_status, nullable=False),
        sa.Column("changed_by_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_id", sa.String(length=100), nullable=True),
        sa.Column("trigger_reason", sa.String(length=255), nullable=True),
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["registration_id"], ["registrations.id"]),
        sa.ForeignKeyConstraint(["changed_by_id"], ["users.id"]),
    )
    op.create_index(op.f("ix_registration_status_history_registration_id"), "registration_status_history", ["registration_id"], unique=False)

    # ── 5. add_drop_requests ─────────────────────────────────────
    op.create_table(
        "add_drop_requests",
        sa.Column("registration_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("target_section_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("action", add_drop_action, nullable=False),
        sa.Column("deadline_snapshot", sa.Date(), nullable=False),
        sa.Column(
            "status", add_drop_request_status,
            nullable=False, server_default="PENDING",
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("override_by_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("override_justification", sa.Text(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["registration_id"], ["registrations.id"]),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"]),
        sa.ForeignKeyConstraint(["target_section_id"], ["sections.id"]),
        sa.ForeignKeyConstraint(["override_by_id"], ["users.id"]),
    )
    op.create_index(op.f("ix_add_drop_requests_registration_id"), "add_drop_requests", ["registration_id"], unique=False)
    op.create_index(op.f("ix_add_drop_requests_course_id"), "add_drop_requests", ["course_id"], unique=False)
    op.create_index(op.f("ix_add_drop_requests_status"), "add_drop_requests", ["status"], unique=False)

    # ── 6. advisory_recommendations ──────────────────────────────
    op.create_table(
        "advisory_recommendations",
        sa.Column("student_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("term_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("risk_status", risk_status, nullable=False),
        sa.Column("risk_explanation", sa.Text(), nullable=False),
        sa.Column("proposed_courses", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("recommended_courses", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("gap_analysis", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("requires_officer_review", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("reviewed_by_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"]),
        sa.ForeignKeyConstraint(["term_id"], ["academic_terms.id"]),
        sa.ForeignKeyConstraint(["reviewed_by_id"], ["users.id"]),
    )
    op.create_index(op.f("ix_advisory_recommendations_student_id"), "advisory_recommendations", ["student_id"], unique=False)
    op.create_index(op.f("ix_advisory_recommendations_term_id"), "advisory_recommendations", ["term_id"], unique=False)
    op.create_index(op.f("ix_advisory_recommendations_risk_status"), "advisory_recommendations", ["risk_status"], unique=False)
    op.create_index(op.f("ix_advisory_recommendations_requires_officer_review"), "advisory_recommendations", ["requires_officer_review"], unique=False)

    # ── 7. prerequisite_overrides ────────────────────────────────
    op.create_table(
        "prerequisite_overrides",
        sa.Column("registration_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("course_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("granted_by_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["registration_id"], ["registrations.id"]),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"]),
        sa.ForeignKeyConstraint(["granted_by_id"], ["users.id"]),
        sa.UniqueConstraint(
            "registration_id", "course_id",
            name="uq_prerequisite_override_per_course",
        ),
    )
    op.create_index(op.f("ix_prerequisite_overrides_registration_id"), "prerequisite_overrides", ["registration_id"], unique=False)
    op.create_index(op.f("ix_prerequisite_overrides_course_id"), "prerequisite_overrides", ["course_id"], unique=False)
    op.create_index(op.f("ix_prerequisite_overrides_granted_by_id"), "prerequisite_overrides", ["granted_by_id"], unique=False)

    # ── 8. schedule_conflicts ────────────────────────────────────
    op.create_table(
        "schedule_conflicts",
        sa.Column("term_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("department", sa.String(length=100), nullable=False),
        sa.Column("conflict_type", schedule_conflict_type, nullable=False),
        sa.Column("section_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("other_section_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("instructor_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("time_slot", sa.String(length=100), nullable=True),
        sa.Column("room", sa.String(length=50), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("detected_by_agent_id", sa.String(length=100), nullable=False),
        sa.Column(
            "status", schedule_conflict_status,
            nullable=False, server_default="OPEN",
        ),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("resolved_by_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["term_id"], ["academic_terms.id"]),
        sa.ForeignKeyConstraint(["section_id"], ["sections.id"]),
        sa.ForeignKeyConstraint(["other_section_id"], ["sections.id"]),
        sa.ForeignKeyConstraint(["instructor_id"], ["instructors.id"]),
        sa.ForeignKeyConstraint(["resolved_by_id"], ["users.id"]),
    )
    op.create_index(op.f("ix_schedule_conflicts_term_id"), "schedule_conflicts", ["term_id"], unique=False)
    op.create_index(op.f("ix_schedule_conflicts_department"), "schedule_conflicts", ["department"], unique=False)
    op.create_index(op.f("ix_schedule_conflicts_conflict_type"), "schedule_conflicts", ["conflict_type"], unique=False)
    op.create_index(op.f("ix_schedule_conflicts_status"), "schedule_conflicts", ["status"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()

    # Drop tables in reverse FK order
    for index_name, table_name in (
        ("ix_schedule_conflicts_status", "schedule_conflicts"),
        ("ix_schedule_conflicts_conflict_type", "schedule_conflicts"),
        ("ix_schedule_conflicts_department", "schedule_conflicts"),
        ("ix_schedule_conflicts_term_id", "schedule_conflicts"),
    ):
        op.drop_index(op.f(index_name), table_name=table_name)
    op.drop_table("schedule_conflicts")

    for index_name, table_name in (
        ("ix_prerequisite_overrides_granted_by_id", "prerequisite_overrides"),
        ("ix_prerequisite_overrides_course_id", "prerequisite_overrides"),
        ("ix_prerequisite_overrides_registration_id", "prerequisite_overrides"),
    ):
        op.drop_index(op.f(index_name), table_name=table_name)
    op.drop_table("prerequisite_overrides")

    for index_name, table_name in (
        ("ix_advisory_recommendations_requires_officer_review", "advisory_recommendations"),
        ("ix_advisory_recommendations_risk_status", "advisory_recommendations"),
        ("ix_advisory_recommendations_term_id", "advisory_recommendations"),
        ("ix_advisory_recommendations_student_id", "advisory_recommendations"),
    ):
        op.drop_index(op.f(index_name), table_name=table_name)
    op.drop_table("advisory_recommendations")

    for index_name, table_name in (
        ("ix_add_drop_requests_status", "add_drop_requests"),
        ("ix_add_drop_requests_course_id", "add_drop_requests"),
        ("ix_add_drop_requests_registration_id", "add_drop_requests"),
    ):
        op.drop_index(op.f(index_name), table_name=table_name)
    op.drop_table("add_drop_requests")

    op.drop_index(op.f("ix_registration_status_history_registration_id"), table_name="registration_status_history")
    op.drop_table("registration_status_history")

    for index_name, table_name in (
        ("ix_registration_courses_section_id", "registration_courses"),
        ("ix_registration_courses_course_id", "registration_courses"),
        ("ix_registration_courses_registration_id", "registration_courses"),
    ):
        op.drop_index(op.f(index_name), table_name=table_name)
    op.drop_table("registration_courses")

    for index_name, table_name in (
        ("ix_registrations_status", "registrations"),
        ("ix_registrations_term_id", "registrations"),
        ("ix_registrations_student_id", "registrations"),
    ):
        op.drop_index(op.f(index_name), table_name=table_name)
    op.drop_table("registrations")

    # Drop the ENUM types Track A creates. We now own all six because
    # Phase 0 no longer pre-creates registrationstatus / adddropaction /
    # riskstatus. sponsorshiptype is owned by phase2_schema and is left
    # alone.
    for enum_name in (
        "scheduleconflictstatus",
        "scheduleconflicttype",
        "adddroprequeststatus",
        "riskstatus",
        "adddropaction",
        "registrationstatus",
    ):
        sa.Enum(name=enum_name).drop(bind, checkfirst=True)
