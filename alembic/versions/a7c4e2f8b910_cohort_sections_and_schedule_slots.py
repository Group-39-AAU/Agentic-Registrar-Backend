"""cohort sections + class schedule slots

Revision ID: a7c4e2f8b910
Revises: f3a9b1d4c8e2
Create Date: 2026-05-04 19:30:00.000000

Reshapes section semantics:
  - Drops admission-side section/portal_password fields from
    enrollments (section moves entirely to course-management; portal
    password lives on User.must_change_password since the credential
    lifecycle work).
  - Replaces per-CourseOffering Section with a per-(term,department,
    semester) cohort Section: one row per group of students who
    attend every course of that semester together in the same room.
  - Adds class_schedule_slots — one row per weekly meeting of a
    course inside a section. Total weekly hours per course = the
    course's credit_hours, so a 3-credit course gets 3 hours/week of
    slots. Day/start/end are now structured columns.
  - Moves the student->section link to Registration.section_id (one
    cohort per registration) and drops the old per-RegistrationCourse
    section_id (which assumed each course had its own section).
  - Drops AddDropRequest.target_section_id — pointless now that a
    student's cohort is fixed for the term.

Tables affected:
  * enrollments        — drop section, drop portal_password
  * sections           — drop offering_id/time_slot/instructor_id;
                         add term_id/department/semester
  * registrations      — add section_id
  * registration_courses — drop section_id
  * add_drop_requests  — drop target_section_id
  * class_schedule_slots — created

Existing seed data in any of these tables is wiped; the seed scripts
recreate everything from scratch on next run.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "a7c4e2f8b910"
down_revision: Union[str, None] = "f3a9b1d4c8e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # ── 1. enrollments — drop section, portal_password ─────────────
    op.drop_column("enrollments", "section")
    op.drop_column("enrollments", "portal_password")

    # ── 2. Wipe rows in tables whose shape is changing so the
    #      structural drops/adds are unambiguous. The seed scripts
    #      reseed on the next run. (No FK cycles need disabling here
    #      because we're going in dependency order.)
    bind.execute(sa.text("DELETE FROM add_drop_requests"))
    bind.execute(sa.text("DELETE FROM registration_status_history"))
    bind.execute(sa.text("DELETE FROM registration_courses"))
    bind.execute(sa.text("DELETE FROM advisory_recommendations"))
    bind.execute(sa.text("DELETE FROM prerequisite_overrides"))
    bind.execute(sa.text("DELETE FROM registrations"))
    bind.execute(sa.text("DELETE FROM schedule_conflicts"))
    bind.execute(sa.text("DELETE FROM instructor_assignments"))
    bind.execute(sa.text("DELETE FROM sections"))
    bind.execute(sa.text("DELETE FROM course_offerings"))

    # ── 3. add_drop_requests — drop target_section_id ──────────────
    op.drop_constraint(
        "add_drop_requests_target_section_id_fkey",
        "add_drop_requests",
        type_="foreignkey",
    )
    op.drop_column("add_drop_requests", "target_section_id")

    # ── 4. registration_courses — drop section_id ──────────────────
    op.drop_constraint(
        "registration_courses_section_id_fkey",
        "registration_courses",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_registration_courses_section_id",
        table_name="registration_courses",
    )
    op.drop_column("registration_courses", "section_id")

    # ── 5. sections — drop the per-offering shape ──────────────────
    op.drop_constraint(
        "uq_section_code_per_offering", "sections", type_="unique",
    )
    op.drop_constraint(
        "sections_offering_id_fkey", "sections", type_="foreignkey",
    )
    op.drop_constraint(
        "sections_instructor_id_fkey", "sections", type_="foreignkey",
    )
    op.drop_index("ix_sections_offering_id", table_name="sections")
    op.drop_index("ix_sections_instructor_id", table_name="sections")
    op.drop_column("sections", "offering_id")
    op.drop_column("sections", "time_slot")
    op.drop_column("sections", "instructor_id")

    # ── 6. sections — add the per-cohort shape ─────────────────────
    op.add_column(
        "sections",
        sa.Column("term_id", postgresql.UUID(as_uuid=True), nullable=False),
    )
    op.create_foreign_key(
        "sections_term_id_fkey", "sections", "academic_terms",
        ["term_id"], ["id"],
    )
    op.create_index(
        "ix_sections_term_id", "sections", ["term_id"],
    )
    op.add_column(
        "sections",
        sa.Column("department", sa.String(length=100), nullable=False),
    )
    op.create_index(
        "ix_sections_department", "sections", ["department"],
    )
    op.add_column(
        "sections",
        sa.Column("semester", sa.Integer(), nullable=False),
    )
    op.create_index(
        "ix_sections_semester", "sections", ["semester"],
    )
    op.create_unique_constraint(
        "uq_section_code_per_term", "sections", ["term_id", "section_code"],
    )
    op.create_check_constraint(
        "ck_sections_semester_range",
        "sections",
        "semester BETWEEN 1 AND 12",
    )

    # ── 7. registrations — add section_id ──────────────────────────
    op.add_column(
        "registrations",
        sa.Column("section_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "registrations_section_id_fkey", "registrations", "sections",
        ["section_id"], ["id"],
    )
    op.create_index(
        "ix_registrations_section_id", "registrations", ["section_id"],
    )

    # ── 8. class_schedule_slots — new table ────────────────────────
    op.create_table(
        "class_schedule_slots",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "section_id", postgresql.UUID(as_uuid=True), nullable=False,
        ),
        sa.Column(
            "course_id", postgresql.UUID(as_uuid=True), nullable=False,
        ),
        sa.Column(
            "instructor_id", postgresql.UUID(as_uuid=True), nullable=True,
        ),
        sa.Column("day_of_week", sa.String(length=3), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["section_id"], ["sections.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"]),
        sa.ForeignKeyConstraint(["instructor_id"], ["instructors.id"]),
        sa.UniqueConstraint(
            "section_id", "day_of_week", "start_time",
            name="uq_section_slot_per_day_start",
        ),
        sa.CheckConstraint(
            "day_of_week IN ('MON','TUE','WED','THU','FRI')",
            name="ck_schedule_slot_day_of_week",
        ),
        sa.CheckConstraint(
            "end_time > start_time",
            name="ck_schedule_slot_end_after_start",
        ),
    )
    op.create_index(
        "ix_class_schedule_slots_section_id",
        "class_schedule_slots", ["section_id"],
    )
    op.create_index(
        "ix_class_schedule_slots_course_id",
        "class_schedule_slots", ["course_id"],
    )
    op.create_index(
        "ix_class_schedule_slots_instructor_id",
        "class_schedule_slots", ["instructor_id"],
    )


def downgrade() -> None:
    # The reshape is intentionally non-trivially reversible (data loss
    # in any direction). Provide a structural inverse so alembic
    # downgrade head-1 doesn't error, but the section/portal_password
    # fields are recreated empty — restoring real data is a manual op.
    op.drop_index(
        "ix_class_schedule_slots_instructor_id",
        table_name="class_schedule_slots",
    )
    op.drop_index(
        "ix_class_schedule_slots_course_id",
        table_name="class_schedule_slots",
    )
    op.drop_index(
        "ix_class_schedule_slots_section_id",
        table_name="class_schedule_slots",
    )
    op.drop_table("class_schedule_slots")

    op.drop_index("ix_registrations_section_id", table_name="registrations")
    op.drop_constraint(
        "registrations_section_id_fkey", "registrations", type_="foreignkey",
    )
    op.drop_column("registrations", "section_id")

    op.drop_constraint("ck_sections_semester_range", "sections", type_="check")
    op.drop_constraint("uq_section_code_per_term", "sections", type_="unique")
    op.drop_index("ix_sections_semester", table_name="sections")
    op.drop_index("ix_sections_department", table_name="sections")
    op.drop_index("ix_sections_term_id", table_name="sections")
    op.drop_column("sections", "semester")
    op.drop_column("sections", "department")
    op.drop_constraint("sections_term_id_fkey", "sections", type_="foreignkey")
    op.drop_column("sections", "term_id")

    op.add_column(
        "sections",
        sa.Column(
            "instructor_id", postgresql.UUID(as_uuid=True), nullable=True,
        ),
    )
    op.add_column(
        "sections",
        sa.Column("time_slot", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "sections",
        sa.Column(
            "offering_id", postgresql.UUID(as_uuid=True), nullable=False,
        ),
    )
    op.create_foreign_key(
        "sections_offering_id_fkey", "sections", "course_offerings",
        ["offering_id"], ["id"],
    )
    op.create_foreign_key(
        "sections_instructor_id_fkey", "sections", "instructors",
        ["instructor_id"], ["id"],
    )
    op.create_index(
        "ix_sections_offering_id", "sections", ["offering_id"],
    )
    op.create_index(
        "ix_sections_instructor_id", "sections", ["instructor_id"],
    )
    op.create_unique_constraint(
        "uq_section_code_per_offering",
        "sections",
        ["offering_id", "section_code"],
    )

    op.add_column(
        "registration_courses",
        sa.Column(
            "section_id", postgresql.UUID(as_uuid=True), nullable=True,
        ),
    )
    op.create_foreign_key(
        "registration_courses_section_id_fkey",
        "registration_courses", "sections",
        ["section_id"], ["id"],
    )
    op.create_index(
        "ix_registration_courses_section_id",
        "registration_courses", ["section_id"],
    )

    op.add_column(
        "add_drop_requests",
        sa.Column(
            "target_section_id", postgresql.UUID(as_uuid=True), nullable=True,
        ),
    )
    op.create_foreign_key(
        "add_drop_requests_target_section_id_fkey",
        "add_drop_requests", "sections",
        ["target_section_id"], ["id"],
    )

    op.add_column(
        "enrollments",
        sa.Column("portal_password", sa.String(length=255), nullable=False,
                  server_default="legacy"),
    )
    op.add_column(
        "enrollments",
        sa.Column("section", sa.String(length=10), nullable=False,
                  server_default="A"),
    )
