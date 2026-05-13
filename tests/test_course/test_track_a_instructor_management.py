"""
Track A — InstructorService (Department-Head instructor lifecycle).

Covers:
  - DH can create an instructor; PIN is generated, hashed onto a new
    User row, must_change_password=True, and the plaintext PIN is
    emailed but not returned.
  - Plain registrar officers and students get 403 on every write.
  - ADMIN escape hatch works.
  - assign_to_course is an upsert: re-posting with a different
    instructor for the same (course, term) silently rebinds.
  - unassign deletes the row.
  - Read endpoints (list_instructors, list_assignments) are open.
"""
from __future__ import annotations

import re
import uuid

import pytest
import pytest_asyncio

from app.core.security import hash_password, verify_password
from app.modules.auth.models import User
from app.modules.course.exceptions import (
    EntityNotFoundError, InvalidAdjustmentRequestError, UnauthorizedActorError,
)
from app.modules.course.models import (
    AcademicTerm, Course, CourseManagementOfficer, Instructor,
    InstructorAssignment,
)
from app.modules.course.service import InstructorService
from app.shared.email import EmailMessage, EmailService
from app.shared.enums import OfficerRole, UserRole


# ── Fakes ────────────────────────────────────────────────────────


class _CapturingEmailService(EmailService):
    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:  # type: ignore[override]
        self.sent.append(message)


# ── Fixtures ─────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def department_head(async_session) -> CourseManagementOfficer:
    user = User(
        id=uuid.uuid4(),
        email="dh-instructor-test@aau.edu.et",
        first_name="Dept", last_name="Head",
        hashed_password=hash_password("x"),
        role=UserRole.REGISTRAR_OFFICER,    # auth-layer role
        is_active=True,
    )
    async_session.add(user)
    await async_session.flush()
    officer = CourseManagementOfficer(
        user_id=user.id,
        staff_id="REG/9100/14",
        role=OfficerRole.DEPARTMENT_HEAD,    # course-management role
        authorization_level=3,
    )
    async_session.add(officer)
    await async_session.flush()
    return officer


@pytest_asyncio.fixture
async def plain_officer(async_session) -> CourseManagementOfficer:
    user = User(
        id=uuid.uuid4(),
        email="reg-instructor-test@aau.edu.et",
        first_name="Reg", last_name="Officer",
        hashed_password=hash_password("x"),
        role=UserRole.REGISTRAR_OFFICER,
        is_active=True,
    )
    async_session.add(user)
    await async_session.flush()
    officer = CourseManagementOfficer(
        user_id=user.id,
        staff_id="REG/9101/14",
        role=OfficerRole.REGISTRAR_OFFICER,
        authorization_level=5,
    )
    async_session.add(officer)
    await async_session.flush()
    return officer


@pytest_asyncio.fixture
async def admin_user(async_session) -> User:
    user = User(
        id=uuid.uuid4(),
        email="admin-instructor-test@aau.edu.et",
        first_name="Sys", last_name="Admin",
        hashed_password=hash_password("x"),
        role=UserRole.ADMIN,
        is_active=True,
    )
    async_session.add(user)
    await async_session.flush()
    return user


@pytest_asyncio.fixture
async def cs_course(async_session) -> Course:
    course = Course(
        code="CS321", title="Operating Systems",
        credit_hours=3, semester=3, department="Computer Science",
    )
    async_session.add(course)
    await async_session.flush()
    return course


@pytest.fixture
def fake_email() -> _CapturingEmailService:
    return _CapturingEmailService()


# ── add_instructor ─────────────────────────────────────────────


async def test_dh_can_add_instructor_with_pin_and_email(
    async_session, department_head, fake_email, cs_course,
):
    svc = InstructorService(async_session, email_service=fake_email)
    instructor, pin = await svc.add_instructor(
        staff_id="STAFF/9999/14",
        email="new-instructor@aau.edu.et",
        first_name="New",
        last_name="Instructor",
        department="Computer Science",
        officer_user_id=department_head.user_id,
    )

    assert re.fullmatch(r"\d{4}", pin)
    assert instructor.instructor_id == "STAFF/9999/14"
    assert instructor.department == "Computer Science"

    # The User row carries role=INSTRUCTOR + must_change_password=True
    user = await async_session.get(User, instructor.user_id)
    assert user.role == UserRole.INSTRUCTOR
    assert user.must_change_password is True
    # PIN authenticates against the stored hash
    assert verify_password(pin, user.hashed_password)

    # PIN was emailed exactly once
    assert len(fake_email.sent) == 1
    msg = fake_email.sent[0]
    assert msg.to_email == "new-instructor@aau.edu.et"
    assert pin in msg.text_body


async def test_admin_can_add_instructor(async_session, admin_user, fake_email):
    svc = InstructorService(async_session, email_service=fake_email)
    instructor, _pin = await svc.add_instructor(
        staff_id="STAFF/9998/14",
        email="admin-added@aau.edu.et",
        first_name="Admin",
        last_name="Added",
        department="Software Engineering",
        officer_user_id=admin_user.id,
    )
    assert instructor.instructor_id == "STAFF/9998/14"


async def test_plain_officer_cannot_add_instructor(
    async_session, plain_officer, fake_email,
):
    svc = InstructorService(async_session, email_service=fake_email)
    with pytest.raises(UnauthorizedActorError):
        await svc.add_instructor(
            staff_id="STAFF/9997/14",
            email="not-allowed@aau.edu.et",
            first_name="Should",
            last_name="Fail",
            department="Computer Science",
            officer_user_id=plain_officer.user_id,
        )


async def test_unknown_caller_cannot_add_instructor(async_session, fake_email):
    svc = InstructorService(async_session, email_service=fake_email)
    with pytest.raises(UnauthorizedActorError):
        await svc.add_instructor(
            staff_id="STAFF/0000/14",
            email="ghost@aau.edu.et",
            first_name="Ghost", last_name="User",
            department="Computer Science",
            officer_user_id=uuid.uuid4(),
        )


async def test_duplicate_staff_id_rejected(
    async_session, department_head, fake_email,
):
    svc = InstructorService(async_session, email_service=fake_email)
    await svc.add_instructor(
        staff_id="STAFF/8888/14",
        email="first@aau.edu.et",
        first_name="First", last_name="Hire",
        department="Computer Science",
        officer_user_id=department_head.user_id,
    )
    with pytest.raises(InvalidAdjustmentRequestError, match="already exists"):
        await svc.add_instructor(
            staff_id="STAFF/8888/14",
            email="second@aau.edu.et",
            first_name="Dup",
            last_name="Hire",
            department="Computer Science",
            officer_user_id=department_head.user_id,
        )


async def test_duplicate_email_rejected(
    async_session, department_head, fake_email,
):
    svc = InstructorService(async_session, email_service=fake_email)
    await svc.add_instructor(
        staff_id="STAFF/7777/14",
        email="shared-email@aau.edu.et",
        first_name="First", last_name="Hire",
        department="Computer Science",
        officer_user_id=department_head.user_id,
    )
    with pytest.raises(InvalidAdjustmentRequestError, match="email"):
        await svc.add_instructor(
            staff_id="STAFF/7776/14",
            email="shared-email@aau.edu.et",
            first_name="Other", last_name="Hire",
            department="Computer Science",
            officer_user_id=department_head.user_id,
        )


# ── assign_to_course (upsert) ──────────────────────────────────


@pytest_asyncio.fixture
async def existing_instructor(async_session) -> Instructor:
    user = User(
        id=uuid.uuid4(),
        email="existing-instr@aau.edu.et",
        first_name="Exist", last_name="Ing",
        hashed_password=hash_password("x"),
        role=UserRole.INSTRUCTOR,
        is_active=True,
    )
    async_session.add(user)
    await async_session.flush()
    instructor = Instructor(
        user_id=user.id,
        instructor_id="STAFF/EXIST/14",
        department="Computer Science",
    )
    async_session.add(instructor)
    await async_session.flush()
    return instructor


@pytest_asyncio.fixture
async def second_instructor(async_session) -> Instructor:
    user = User(
        id=uuid.uuid4(),
        email="second-instr@aau.edu.et",
        first_name="Second", last_name="Instructor",
        hashed_password=hash_password("x"),
        role=UserRole.INSTRUCTOR,
        is_active=True,
    )
    async_session.add(user)
    await async_session.flush()
    instructor = Instructor(
        user_id=user.id,
        instructor_id="STAFF/SECOND/14",
        department="Computer Science",
    )
    async_session.add(instructor)
    await async_session.flush()
    return instructor


async def test_dh_can_assign_instructor_to_course(
    async_session, department_head, cs_course, seeded_term, existing_instructor,
):
    svc = InstructorService(async_session)
    assignment = await svc.assign_to_course(
        instructor_id=existing_instructor.id,
        course_id=cs_course.id,
        term_id=seeded_term.id,
        officer_user_id=department_head.user_id,
    )
    assert assignment.instructor_id == existing_instructor.id
    assert assignment.course_id == cs_course.id
    assert assignment.term_id == seeded_term.id


async def test_assign_is_upsert_rebinds_in_place(
    async_session, department_head, cs_course, seeded_term,
    existing_instructor, second_instructor,
):
    """Re-posting with a different instructor for the same (course,
    term) silently rebinds — at most one active assignment."""
    svc = InstructorService(async_session)
    first = await svc.assign_to_course(
        instructor_id=existing_instructor.id,
        course_id=cs_course.id,
        term_id=seeded_term.id,
        officer_user_id=department_head.user_id,
    )
    second = await svc.assign_to_course(
        instructor_id=second_instructor.id,
        course_id=cs_course.id,
        term_id=seeded_term.id,
        officer_user_id=department_head.user_id,
    )
    # Same row id (it was updated, not duplicated)
    assert first.id == second.id
    # Now points at the second instructor
    assert second.instructor_id == second_instructor.id


async def test_plain_officer_cannot_assign(
    async_session, plain_officer, cs_course, seeded_term, existing_instructor,
):
    svc = InstructorService(async_session)
    with pytest.raises(UnauthorizedActorError):
        await svc.assign_to_course(
            instructor_id=existing_instructor.id,
            course_id=cs_course.id,
            term_id=seeded_term.id,
            officer_user_id=plain_officer.user_id,
        )


async def test_assign_404_on_unknown_instructor(
    async_session, department_head, cs_course, seeded_term,
):
    svc = InstructorService(async_session)
    with pytest.raises(EntityNotFoundError, match="Instructor"):
        await svc.assign_to_course(
            instructor_id=uuid.uuid4(),
            course_id=cs_course.id,
            term_id=seeded_term.id,
            officer_user_id=department_head.user_id,
        )


async def test_assign_404_on_unknown_course(
    async_session, department_head, seeded_term, existing_instructor,
):
    svc = InstructorService(async_session)
    with pytest.raises(EntityNotFoundError, match="Course"):
        await svc.assign_to_course(
            instructor_id=existing_instructor.id,
            course_id=uuid.uuid4(),
            term_id=seeded_term.id,
            officer_user_id=department_head.user_id,
        )


# ── unassign ───────────────────────────────────────────────────


async def test_dh_can_unassign(
    async_session, department_head, cs_course, seeded_term, existing_instructor,
):
    svc = InstructorService(async_session)
    assignment = await svc.assign_to_course(
        instructor_id=existing_instructor.id,
        course_id=cs_course.id,
        term_id=seeded_term.id,
        officer_user_id=department_head.user_id,
    )
    await svc.unassign(
        assignment_id=assignment.id,
        officer_user_id=department_head.user_id,
    )
    # The row is gone
    assert await async_session.get(InstructorAssignment, assignment.id) is None


async def test_plain_officer_cannot_unassign(
    async_session, department_head, plain_officer, cs_course, seeded_term,
    existing_instructor,
):
    svc = InstructorService(async_session)
    assignment = await svc.assign_to_course(
        instructor_id=existing_instructor.id,
        course_id=cs_course.id,
        term_id=seeded_term.id,
        officer_user_id=department_head.user_id,
    )
    with pytest.raises(UnauthorizedActorError):
        await svc.unassign(
            assignment_id=assignment.id,
            officer_user_id=plain_officer.user_id,
        )


# ── Read endpoints (open to any logged-in user) ────────────────


async def test_list_instructors_filters_by_department(
    async_session, department_head, fake_email,
):
    svc = InstructorService(async_session, email_service=fake_email)
    await svc.add_instructor(
        staff_id="STAFF/D001/14", email="d1@aau.edu.et",
        first_name="Cs", last_name="One",
        department="Computer Science",
        officer_user_id=department_head.user_id,
    )
    await svc.add_instructor(
        staff_id="STAFF/D002/14", email="d2@aau.edu.et",
        first_name="Se", last_name="Two",
        department="Software Engineering",
        officer_user_id=department_head.user_id,
    )
    cs_only = await svc.list_instructors(department="Computer Science")
    assert {i.instructor_id for i in cs_only} == {"STAFF/D001/14"}

    everything = await svc.list_instructors()
    assert {"STAFF/D001/14", "STAFF/D002/14"} <= {
        i.instructor_id for i in everything
    }


async def test_list_assignments_filters_correctly(
    async_session, department_head, cs_course, seeded_term,
    existing_instructor, second_instructor,
):
    svc = InstructorService(async_session)
    other_course = Course(
        code="CS322", title="Networks",
        credit_hours=3, semester=3, department="Computer Science",
    )
    async_session.add(other_course)
    await async_session.flush()

    a1 = await svc.assign_to_course(
        instructor_id=existing_instructor.id,
        course_id=cs_course.id,
        term_id=seeded_term.id,
        officer_user_id=department_head.user_id,
    )
    a2 = await svc.assign_to_course(
        instructor_id=second_instructor.id,
        course_id=other_course.id,
        term_id=seeded_term.id,
        officer_user_id=department_head.user_id,
    )

    by_course = await svc.list_assignments(course_id=cs_course.id)
    assert {a.id for a in by_course} == {a1.id}

    by_instructor = await svc.list_assignments(
        instructor_id=second_instructor.id,
    )
    assert {a.id for a in by_instructor} == {a2.id}

    by_term = await svc.list_assignments(term_id=seeded_term.id)
    assert {a1.id, a2.id} <= {a.id for a in by_term}
