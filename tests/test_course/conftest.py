"""
Course Management — test fixtures.

Builds on the root conftest's ``async_session`` fixture and adds
helpers that materialise a minimal-but-realistic graph of seeded
entities (one term, one course, one instructor, one offering, one
section, one student, one officer) for any test that needs a
working catalog.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest_asyncio

from app.modules.auth.models import User
from app.modules.course.models import (
    AcademicTerm, Course, CourseOffering, CourseManagementOfficer,
    Instructor, Section, Student,
)
from app.shared.enums import EnrollmentStatus, OfficerRole, UserRole


def _new_user(role: UserRole = UserRole.STUDENT, *, email_slug: str) -> User:
    """Helper: build a minimally-valid User row."""
    return User(
        id=uuid.uuid4(),
        email=f"{email_slug}@aau.edu.et",
        first_name="Test",
        last_name="User",
        hashed_password="not-a-real-hash",
        role=role,
        is_active=True,
    )


@pytest_asyncio.fixture
async def seeded_term(async_session) -> AcademicTerm:
    term = AcademicTerm(
        term_name="Fall 2026",
        start_date=date(2026, 9, 1),
        end_date=date(2027, 1, 31),
        is_open=True,
    )
    async_session.add(term)
    await async_session.flush()
    return term


@pytest_asyncio.fixture
async def seeded_course(async_session) -> Course:
    course = Course(
        code="CS101",
        title="Introduction to Programming",
        credit_hours=4,
        semester=1,
        department="Computer Science",
    )
    async_session.add(course)
    await async_session.flush()
    return course


@pytest_asyncio.fixture
async def seeded_instructor(async_session) -> Instructor:
    user = _new_user(role=UserRole.AGENT, email_slug="staff-0001-10")
    async_session.add(user)
    await async_session.flush()

    instructor = Instructor(
        user_id=user.id,
        instructor_id="STAFF/0001/10",
        department="Computer Science",
    )
    async_session.add(instructor)
    await async_session.flush()
    return instructor


@pytest_asyncio.fixture
async def seeded_offering(
    async_session, seeded_term, seeded_course,
) -> CourseOffering:
    offering = CourseOffering(
        course_id=seeded_course.id,
        term_id=seeded_term.id,
        capacity=30,
        section_count=1,
    )
    async_session.add(offering)
    await async_session.flush()
    return offering


@pytest_asyncio.fixture
async def seeded_section(
    async_session, seeded_offering, seeded_instructor,
) -> Section:
    section = Section(
        offering_id=seeded_offering.id,
        section_code="A",
        room="NB-101",
        time_slot="MON 08:30-10:00, WED 08:30-10:00",
        instructor_id=seeded_instructor.id,
        capacity=30,
        enrolled_count=0,
    )
    async_session.add(section)
    await async_session.flush()
    return section


@pytest_asyncio.fixture
async def seeded_student(async_session) -> Student:
    user = _new_user(email_slug="ugr-0001-14")
    async_session.add(user)
    await async_session.flush()

    student = Student(
        user_id=user.id,
        student_id="UGR/0001/14",
        full_name="Test Student",
        current_semester=1,
        enrollment_status=EnrollmentStatus.ACTIVE,
    )
    async_session.add(student)
    await async_session.flush()
    return student


@pytest_asyncio.fixture
async def seeded_officer(async_session) -> CourseManagementOfficer:
    user = _new_user(role=UserRole.REGISTRAR_OFFICER, email_slug="registrar-officer")
    async_session.add(user)
    await async_session.flush()

    officer = CourseManagementOfficer(
        user_id=user.id,
        staff_id="REG/9001/10",
        role=OfficerRole.REGISTRAR_OFFICER,
        authorization_level=5,
    )
    async_session.add(officer)
    await async_session.flush()
    return officer
