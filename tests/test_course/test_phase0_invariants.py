"""
Phase 0 — column and constraint invariant tests.

These tests bypass the application layer and write directly to the
database to confirm that every CHECK / UNIQUE / FK constraint defined
on the Phase-0 entities is actually wired up. They are the safety net
that catches schema drift between models.py and the alembic migration.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.auth.models import User
from app.modules.course.models import (
    Course, CourseManagementOfficer, CoursePrerequisite, CourseOffering,
    Section, Student,
)
from app.shared.enums import EnrollmentStatus, OfficerRole, UserRole


# ── Course constraints ──────────────────────────────────────────


async def test_course_credit_hours_must_be_in_range(async_session):
    bad = Course(
        code="BAD-CREDIT",
        title="Out of range",
        credit_hours=99,        # CHECK: 1..12
        semester=1,
        department="Test",
    )
    async_session.add(bad)
    with pytest.raises(IntegrityError):
        await async_session.flush()


async def test_course_semester_must_be_in_range(async_session):
    bad = Course(
        code="BAD-SEMESTER",
        title="Out of range",
        credit_hours=3,
        semester=99,            # CHECK: 1..12
        department="Test",
    )
    async_session.add(bad)
    with pytest.raises(IntegrityError):
        await async_session.flush()


async def test_course_code_is_unique(async_session, seeded_course):
    duplicate = Course(
        code=seeded_course.code,    # UNIQUE
        title="Duplicate code",
        credit_hours=3,
        semester=1,
        department="Test",
    )
    async_session.add(duplicate)
    with pytest.raises(IntegrityError):
        await async_session.flush()


# ── CoursePrerequisite constraints ──────────────────────────────


async def test_prereq_cannot_point_to_self(async_session, seeded_course):
    self_loop = CoursePrerequisite(
        course_id=seeded_course.id,
        prerequisite_course_id=seeded_course.id,    # CHECK: <>
    )
    async_session.add(self_loop)
    with pytest.raises(IntegrityError):
        await async_session.flush()


# ── CourseOffering constraints ──────────────────────────────────


async def test_offering_capacity_must_be_positive(
    async_session, seeded_term, seeded_course,
):
    bad = CourseOffering(
        course_id=seeded_course.id,
        term_id=seeded_term.id,
        capacity=0,             # CHECK: > 0
        section_count=1,
    )
    async_session.add(bad)
    with pytest.raises(IntegrityError):
        await async_session.flush()


# ── Section constraints ─────────────────────────────────────────


async def test_section_enrolled_cannot_exceed_capacity(
    async_session, seeded_term,
):
    bad = Section(
        term_id=seeded_term.id,
        department="Computer Science",
        semester=1,
        section_code="OVERFLOW",
        capacity=10,
        enrolled_count=11,      # CHECK: enrolled <= capacity
    )
    async_session.add(bad)
    with pytest.raises(IntegrityError):
        await async_session.flush()


# ── Student constraints ─────────────────────────────────────────


async def test_student_current_semester_must_be_in_range(async_session):
    user = User(
        id=uuid.uuid4(),
        email="bad-sem@aau.edu.et",
        first_name="Bad", last_name="Semester",
        hashed_password="x", role=UserRole.STUDENT, is_active=True,
    )
    async_session.add(user)
    await async_session.flush()

    bad = Student(
        user_id=user.id,
        student_id="UGR/9999/14",
        full_name="Out of range",
        current_semester=99,    # CHECK: 1..12
        enrollment_status=EnrollmentStatus.ACTIVE,
    )
    async_session.add(bad)
    with pytest.raises(IntegrityError):
        await async_session.flush()


# ── CourseManagementOfficer constraints ─────────────────────────


async def test_officer_authorization_level_must_be_in_range(async_session):
    user = User(
        id=uuid.uuid4(),
        email="bad-level@aau.edu.et",
        first_name="Bad", last_name="Level",
        hashed_password="x", role=UserRole.REGISTRAR_OFFICER, is_active=True,
    )
    async_session.add(user)
    await async_session.flush()

    bad = CourseManagementOfficer(
        user_id=user.id,
        staff_id="REG/9999/10",
        role=OfficerRole.REGISTRAR_OFFICER,
        authorization_level=99,     # CHECK: 1..5
    )
    async_session.add(bad)
    with pytest.raises(IntegrityError):
        await async_session.flush()
