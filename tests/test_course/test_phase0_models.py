"""
Phase 0 — model relationship round-trip tests.

Each test creates a small graph of entities, flushes it, then re-reads
through the relationship loaders to confirm that every FK and
back-reference resolves end-to-end. SQLite is used as the test
backend so the suite is fast and dependency-free.
"""
from __future__ import annotations

from sqlalchemy import select

from app.modules.course.models import (
    AcademicTerm, Course, CourseOffering, CoursePrerequisite, Instructor,
    InstructorAssignment, Section,
)


async def test_term_round_trip(async_session, seeded_term):
    fetched = (
        await async_session.execute(
            select(AcademicTerm).where(AcademicTerm.term_name == "Fall 2026")
        )
    ).scalar_one()
    assert fetched.id == seeded_term.id
    assert fetched.is_open is True
    assert fetched.is_deleted is False


async def test_course_round_trip(async_session, seeded_course):
    fetched = (
        await async_session.execute(
            select(Course).where(Course.code == "CS101")
        )
    ).scalar_one()
    assert fetched.id == seeded_course.id
    assert fetched.title == "Introduction to Programming"
    assert fetched.credit_hours == 4


async def test_offering_resolves_course_and_term(
    async_session, seeded_offering, seeded_course, seeded_term,
):
    fetched = (
        await async_session.execute(
            select(CourseOffering).where(CourseOffering.id == seeded_offering.id)
        )
    ).scalar_one()
    assert fetched.course.code == seeded_course.code
    assert fetched.term.term_name == seeded_term.term_name


async def test_section_resolves_offering(
    async_session, seeded_section, seeded_offering, seeded_instructor,
):
    fetched = (
        await async_session.execute(
            select(Section).where(Section.id == seeded_section.id)
        )
    ).scalar_one()
    assert fetched.offering.id == seeded_offering.id
    assert fetched.instructor_id == seeded_instructor.id


async def test_prerequisite_resolves_both_endpoints(
    async_session, seeded_course,
):
    advanced = Course(
        code="CS201",
        title="Data Structures",
        credit_hours=4,
        semester=2,
        department="Computer Science",
    )
    async_session.add(advanced)
    await async_session.flush()

    edge = CoursePrerequisite(
        course_id=advanced.id,
        prerequisite_course_id=seeded_course.id,
    )
    async_session.add(edge)
    await async_session.flush()

    fetched = (
        await async_session.execute(
            select(CoursePrerequisite).where(CoursePrerequisite.id == edge.id)
        )
    ).scalar_one()
    assert fetched.course.code == "CS201"
    assert fetched.prerequisite_course.code == "CS101"


async def test_instructor_assignment_round_trip(
    async_session, seeded_term, seeded_course, seeded_instructor,
):
    assn = InstructorAssignment(
        instructor_id=seeded_instructor.id,
        course_id=seeded_course.id,
        term_id=seeded_term.id,
    )
    async_session.add(assn)
    await async_session.flush()

    fetched = (
        await async_session.execute(
            select(InstructorAssignment).where(InstructorAssignment.id == assn.id)
        )
    ).scalar_one()
    assert fetched.instructor.instructor_id == "STAFF/0001/10"
    assert fetched.course.code == "CS101"
    assert fetched.term.term_name == "Fall 2026"


async def test_student_round_trip(async_session, seeded_student):
    from app.modules.course.models import Student
    fetched = (
        await async_session.execute(
            select(Student).where(Student.student_id == "UGR/0001/14")
        )
    ).scalar_one()
    assert fetched.id == seeded_student.id
    assert fetched.full_name == "Test Student"
    assert fetched.current_semester == 1


async def test_officer_round_trip(async_session, seeded_officer):
    from app.modules.course.models import CourseManagementOfficer
    from app.shared.enums import OfficerRole

    fetched = (
        await async_session.execute(
            select(CourseManagementOfficer).where(
                CourseManagementOfficer.staff_id == "REG/9001/10"
            )
        )
    ).scalar_one()
    assert fetched.role == OfficerRole.REGISTRAR_OFFICER
    assert fetched.authorization_level == 5
