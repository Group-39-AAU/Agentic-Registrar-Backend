"""
Phase 0 — enum persistence round-trip tests.

Each Course-Management enum is written to its column and re-read to
confirm SQLAlchemy's enum codec preserves the exact value. These
tests are the canary for the SRS/SDS-mandated string values (e.g.
the SDS Figure 39 state names) — if anyone tweaks an enum member,
these tests fail and force the migration to be updated in lockstep.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.modules.auth.models import User
from app.modules.course.models import (
    CourseManagementOfficer, Student,
)
from app.shared.enums import (
    AcademicStatusType, AddDropAction, AgentStatus, EnrollmentStatus,
    ExceptionStatus, GradeLetter, GradeSubmissionStatus, OfficerRole,
    RegistrationStatus, RiskStatus, UserRole,
)


async def test_enrollment_status_round_trip(async_session):
    user = User(
        id=uuid.uuid4(),
        email="enroll-status@aau.edu.et",
        first_name="Test", last_name="Student",
        hashed_password="x", role=UserRole.STUDENT, is_active=True,
    )
    async_session.add(user)
    await async_session.flush()

    student = Student(
        user_id=user.id,
        student_id="UGR/0500/14",
        full_name="Test Student",
        current_semester=2,
        enrollment_status=EnrollmentStatus.WITHDRAWN,
    )
    async_session.add(student)
    await async_session.flush()

    fetched = (
        await async_session.execute(
            select(Student).where(Student.id == student.id)
        )
    ).scalar_one()
    assert fetched.enrollment_status == EnrollmentStatus.WITHDRAWN


async def test_officer_role_round_trip(async_session):
    user = User(
        id=uuid.uuid4(),
        email="dept-head-rt@aau.edu.et",
        first_name="Dept", last_name="Head",
        hashed_password="x", role=UserRole.REGISTRAR_OFFICER, is_active=True,
    )
    async_session.add(user)
    await async_session.flush()

    officer = CourseManagementOfficer(
        user_id=user.id,
        staff_id="REG/0500/10",
        role=OfficerRole.DEPARTMENT_HEAD,
        authorization_level=3,
    )
    async_session.add(officer)
    await async_session.flush()

    fetched = (
        await async_session.execute(
            select(CourseManagementOfficer).where(
                CourseManagementOfficer.id == officer.id
            )
        )
    ).scalar_one()
    assert fetched.role == OfficerRole.DEPARTMENT_HEAD


def test_registration_status_values_match_sds_figure_39():
    """The Figure-39 state names are load-bearing — guard them here."""
    assert {s.value for s in RegistrationStatus} == {
        "REGISTRATION_OPEN", "ADVISOR_REVIEW", "CHECKING_PREREQUISITES",
        "CHECKING_PAYMENT", "PAYMENT_HOLD", "VALIDATION_SUCCESS",
        "REGISTERED", "ADD_DROP_WINDOW", "CANCELLED",
    }


def test_agent_status_values_match_sds_table_86():
    assert {s.value for s in AgentStatus} == {
        "IDLE", "BUSY", "WAITING_HUMAN", "ERROR",
    }


def test_grade_letter_includes_special_marks():
    """I (Incomplete) and NG (No Grade) drive the handleEdgeCase path."""
    values = {g.value for g in GradeLetter}
    assert "I" in values
    assert "NG" in values
    # Letter grades match the published AAU scale
    assert {"A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D", "F"}.issubset(values)


def test_academic_status_type_matches_sds_table_74():
    assert {s.value for s in AcademicStatusType} == {
        "PROMOTED", "WARNING", "DISTINCTION", "DISMISSED", "INCOMPLETE",
    }


def test_misc_enum_value_sets_are_stable():
    assert {s.value for s in AddDropAction} == {"ADD", "DROP"}
    assert {s.value for s in ExceptionStatus} == {
        "OPEN", "IN_REVIEW", "RESOLVED", "ESCALATED",
    }
    assert {s.value for s in GradeSubmissionStatus} == {
        "DRAFT", "SUBMITTED", "FLAGGED", "AUTHORISED", "REJECTED",
    }
    assert {s.value for s in OfficerRole} == {
        "REGISTRAR_OFFICER", "DEPARTMENT_HEAD",
    }
    assert {s.value for s in RiskStatus} == {"LOW", "MEDIUM", "HIGH"}
