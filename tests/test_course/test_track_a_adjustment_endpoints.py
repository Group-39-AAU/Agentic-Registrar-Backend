"""
Track A — add/drop notification + email-hook integration tests.

Verifies the AddDropService correctly drives the
notify_adjustment_success hook into the EmailService when one is
injected. Follows the existing Track A test convention of driving
through the service layer rather than HTTP.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.modules.course.agents import EnrollmentAdjustmentAgent
from app.modules.course.models import (
    AddDropRequest, Course, CourseOffering, Registration, RegistrationCourse,
    Section,
)
from app.modules.course.service import AddDropService
from app.modules.course.services import PayMock
from app.shared.email.schemas import EmailMessage
from app.shared.enums import (
    AddDropAction, AddDropRequestStatus, RegistrationStatus,
    SponsorshipType, UserRole,
)


# ── Recording email service (test double) ───────────────────────


class _RecordingEmailService:
    """Test double — records every send() call without hitting SMTP."""

    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> bool:
        self.sent.append(message)
        return True


# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture
def isolated_pay_mock() -> PayMock:
    return PayMock()


@pytest.fixture
def recording_email() -> _RecordingEmailService:
    return _RecordingEmailService()


@pytest.fixture
def add_drop_service(
    async_session, isolated_pay_mock, recording_email,
) -> AddDropService:
    agent = EnrollmentAdjustmentAgent(
        agent_id="AGENT_EAA_TEST",
        payment_service=isolated_pay_mock,
    )
    return AddDropService(
        async_session,
        adjustment_agent=agent,
        email_service=recording_email,
    )


@pytest_asyncio.fixture
async def cs_courses(async_session) -> list[Course]:
    courses = [
        Course(code=f"CS10{i}", title=f"Course {i}", credit_hours=4,
               semester=1, department="Computer Science")
        for i in range(1, 4)
    ]
    async_session.add_all(courses)
    await async_session.flush()
    return courses


@pytest_asyncio.fixture
async def extra_course_with_offering(async_session, seeded_term):
    course = Course(
        code="MATH101", title="Calc I", credit_hours=4,
        semester=1, department="Mathematics",
    )
    async_session.add(course)
    await async_session.flush()
    offering = CourseOffering(
        course_id=course.id, term_id=seeded_term.id,
        capacity=30, section_count=1,
    )
    async_session.add(offering)
    await async_session.flush()
    section = Section(
        offering_id=offering.id, section_code="A",
        room="NB-101", time_slot="MON 08:30-10:00, WED 08:30-10:00",
        capacity=30, enrolled_count=0,
    )
    async_session.add(section)
    await async_session.flush()
    return {"course": course, "offering": offering, "section": section}


@pytest_asyncio.fixture
async def registration_at_floor(
    async_session, seeded_term, seeded_student, cs_courses,
) -> Registration:
    """REGISTERED registration at exactly 12 ECTS."""
    reg = Registration(
        student_id=seeded_student.id, term_id=seeded_term.id,
        status=RegistrationStatus.REGISTERED,
        sponsorship_type=SponsorshipType.SELF_SPONSORED,
    )
    async_session.add(reg)
    await async_session.flush()
    for c in cs_courses:
        async_session.add(RegistrationCourse(
            registration_id=reg.id, course_id=c.id,
        ))
    await async_session.flush()
    await async_session.refresh(reg, attribute_names=["courses"])
    return reg


# ── Notification hook tests ─────────────────────────────────────


async def test_email_sent_on_successful_ADD(
    add_drop_service, isolated_pay_mock, recording_email,
    registration_at_floor, extra_course_with_offering, seeded_student,
):
    isolated_pay_mock.set_payment_status(
        seeded_student.id, extra_course_with_offering["course"].id, paid=True,
    )
    request = await add_drop_service.submit_request(
        registration_id=registration_at_floor.id,
        course_id=extra_course_with_offering["course"].id,
        action=AddDropAction.ADD,
        deadline=date(2099, 1, 1),
        student_user_id=uuid.uuid4(),
    )
    assert request.status == AddDropRequestStatus.APPLIED
    assert len(recording_email.sent) == 1
    msg = recording_email.sent[0]
    assert "ADD" in msg.text_body or "ADD" in (msg.html_body or "")
    assert msg.subject.startswith("Your course adjustment")


async def test_no_email_when_email_service_not_injected(
    async_session, isolated_pay_mock,
    registration_at_floor, extra_course_with_offering, seeded_student,
):
    """
    AddDropService constructed without email_service must not raise
    when the notify hook fires; the audit-log row is still emitted.
    """
    isolated_pay_mock.set_payment_status(
        seeded_student.id, extra_course_with_offering["course"].id, paid=True,
    )
    agent = EnrollmentAdjustmentAgent(
        agent_id="AGENT_EAA_NOEMAIL",
        payment_service=isolated_pay_mock,
    )
    svc = AddDropService(async_session, adjustment_agent=agent)

    request = await svc.submit_request(
        registration_id=registration_at_floor.id,
        course_id=extra_course_with_offering["course"].id,
        action=AddDropAction.ADD,
        deadline=date(2099, 1, 1),
        student_user_id=uuid.uuid4(),
    )
    assert request.status == AddDropRequestStatus.APPLIED


async def test_email_sent_after_officer_override(
    async_session, add_drop_service, recording_email,
    registration_at_floor, cs_courses, seeded_student, seeded_officer,
):
    from app.modules.course.exceptions import AdjustmentDeniedError
    with pytest.raises(AdjustmentDeniedError):
        await add_drop_service.submit_request(
            registration_id=registration_at_floor.id,
            course_id=cs_courses[0].id,
            action=AddDropAction.DROP,
            deadline=date(2099, 1, 1),
            student_user_id=seeded_student.user_id,
        )
    await async_session.commit()
    assert recording_email.sent == []     # no email on denial

    denied = (
        await async_session.execute(
            select(AddDropRequest).where(
                AddDropRequest.registration_id == registration_at_floor.id,
            )
        )
    ).scalar_one()

    await add_drop_service.officer_override(
        request_id=denied.id,
        officer_role=UserRole.REGISTRAR_OFFICER,
        officer_id=seeded_officer.user_id,
        justification="medical exemption documented",
    )
    assert len(recording_email.sent) == 1   # one email fired on override
    assert "DROP" in recording_email.sent[0].text_body
