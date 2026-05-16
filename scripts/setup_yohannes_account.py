"""
One-shot bootstrap for the user yohannes.abdia@gmail.com.

Lands the account in the post-onboarding state with a known portal
password (``pass1234``) so the user can jump straight into Track A
testing without driving the full admission lifecycle through Swagger.

Idempotent: re-running just resets the password and clears the
must_change_password flag.

Steps performed:
  1. Reuse / create an admission term (open by default).
  2. Reuse / create the User row.
  3. Reuse / create an ENROLLED application + Enrollment row with a
     fresh UGR/XXXX/YY id.
  4. Reuse / create the course-management Student row.
  5. Force ``hashed_password = hash_password('pass1234')`` and
     ``must_change_password = False`` on the User.

Run:
    ./venv/bin/python scripts/setup_yohannes_account.py
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.database.session import AsyncSessionLocal

# ── Model registry: every cross-module FK target must be imported so
#    SQLAlchemy's mapper can resolve them when we touch the metadata
#    graph in this short-lived script.
from app.modules.auth.models import User                                     # noqa: F401
from app.modules.programs.models import AcademicProgram                      # noqa: F401
from app.shared.audit.models import SystemAuditLog                           # noqa: F401
from app.modules.undergraduate.models import (                               # noqa: F401
    UndergraduateAdmissionTerm, UndergraduateApplication,
    ApplicationDocument, ApplicationStatusHistory, RegistrarDecision,
)
from app.ai.models import AIEvaluation, AIExecutionTrace                     # noqa: F401
from app.modules.moe.models import MoeStudentRecord                          # noqa: F401
from app.modules.testing_center.models import UATRecord                      # noqa: F401
from app.modules.undergraduate.ranking.models import (                       # noqa: F401
    StreamQuota, RankingResult,
)
from app.modules.undergraduate.enrollment.models import Enrollment           # noqa: F401
from app.modules.course.models import (                                      # noqa: F401
    AcademicTerm, Course, CoursePrerequisite, Section, ClassScheduleSlot,
    Classroom,
    Student, Instructor, InstructorAssignment, CourseManagementOfficer,
    Registration, RegistrationCourse, RegistrationStatusHistory,
    AddDropRequest, AdvisoryRecommendation, PrerequisiteOverride,
    ScheduleConflict,
)

from app.shared.enums import (
    ApplicationStatus,
    EnrollmentStatus,
    SponsorshipType,
    StreamType,
    UserRole,
)


TARGET_EMAIL = "yohannes.abdia@gmail.com"
TARGET_PASSWORD = "pass1234"
TARGET_STUDENT_ID = "UGR/9999/14"          # Free per current seed
TARGET_UNIVERSITY_ID = TARGET_STUDENT_ID    # Same value lives on Enrollment.university_id
TARGET_DEPARTMENT = "Software Engineering"
TARGET_SPONSORSHIP = SponsorshipType.SELF_SPONSORED
ADMISSION_TERM_NAME = "2026/2027 Round 1 (manual bootstrap)"


async def _get_or_create_admission_term(db: AsyncSession) -> UndergraduateAdmissionTerm:
    existing = (
        await db.execute(
            select(UndergraduateAdmissionTerm).where(
                UndergraduateAdmissionTerm.term_name == ADMISSION_TERM_NAME
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    term = UndergraduateAdmissionTerm(
        id=uuid.uuid4(),
        term_name=ADMISSION_TERM_NAME,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 8, 31),
        is_open=True,
        description="Manually-bootstrapped term for end-to-end course-management testing.",
    )
    db.add(term)
    await db.flush()
    print(f"  ✅ Created admission term: {term.term_name}")
    return term


async def _get_or_create_user(db: AsyncSession) -> User:
    existing = (
        await db.execute(select(User).where(User.email == TARGET_EMAIL))
    ).scalar_one_or_none()
    if existing is not None:
        print(f"  ↻  User already exists: {existing.email}")
        return existing

    user = User(
        id=uuid.uuid4(),
        email=TARGET_EMAIL,
        first_name="Yohannes",
        last_name="Abdia",
        hashed_password=hash_password(TARGET_PASSWORD),
        role=UserRole.STUDENT,
        is_active=True,
        must_change_password=False,
    )
    db.add(user)
    await db.flush()
    print(f"  ✅ Created user: {user.email}")
    return user


async def _get_or_create_application(
    db: AsyncSession, user: User, term: UndergraduateAdmissionTerm,
) -> UndergraduateApplication:
    existing = (
        await db.execute(
            select(UndergraduateApplication).where(
                UndergraduateApplication.applicant_id == user.id,
                UndergraduateApplication.admission_term_id == term.id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        print(f"  ↻  Application already exists (id={existing.id})")
        return existing

    app_row = UndergraduateApplication(
        id=uuid.uuid4(),
        applicant_id=user.id,
        admission_term_id=term.id,
        admission_number="9999999",
        sponsorship_type=TARGET_SPONSORSHIP,
        stream=StreamType.NATURAL,
        current_status=ApplicationStatus.ENROLLED,
    )
    db.add(app_row)
    await db.flush()
    print(f"  ✅ Created application (status=ENROLLED)")
    return app_row


async def _get_or_create_enrollment(
    db: AsyncSession,
    user: User,
    app_row: UndergraduateApplication,
    term: UndergraduateAdmissionTerm,
) -> Enrollment:
    existing = (
        await db.execute(
            select(Enrollment).where(Enrollment.applicant_id == user.id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        # Reconcile department + admission_term_id in case the catalog
        # moved underneath us (e.g. earlier bootstrap used a different
        # department label, or predates the admission_term_id column).
        changes: list[str] = []
        if existing.department != TARGET_DEPARTMENT:
            existing.department = TARGET_DEPARTMENT
            changes.append("department")
        if existing.admission_term_id != term.id:
            existing.admission_term_id = term.id
            changes.append("admission_term_id")
        await db.flush()
        suffix = f" — reconciled: {', '.join(changes)}" if changes else ""
        print(
            f"  ↻  Enrollment already exists "
            f"(university_id={existing.university_id}){suffix}"
        )
        return existing

    enrollment = Enrollment(
        id=uuid.uuid4(),
        application_id=app_row.id,
        applicant_id=user.id,
        admission_term_id=term.id,
        university_id=TARGET_UNIVERSITY_ID,
        department=TARGET_DEPARTMENT,
        enrollment_term="2026/2027",
    )
    db.add(enrollment)
    await db.flush()
    print(f"  ✅ Created enrollment: {enrollment.university_id}")
    return enrollment


async def _get_or_create_student(db: AsyncSession, user: User) -> Student:
    existing = (
        await db.execute(select(Student).where(Student.user_id == user.id))
    ).scalar_one_or_none()
    if existing is not None:
        # Reconcile every denormalised admission attribute on every
        # run so the curriculum/sponsorship filters always match the
        # current target state.
        changes: list[str] = []
        if existing.department != TARGET_DEPARTMENT:
            existing.department = TARGET_DEPARTMENT
            changes.append("department")
        if existing.sponsorship_type != TARGET_SPONSORSHIP:
            existing.sponsorship_type = TARGET_SPONSORSHIP
            changes.append("sponsorship_type")
        if changes:
            await db.flush()
            print(
                f"  ↻  Student row already exists "
                f"(student_id={existing.student_id}) — reconciled: "
                f"{', '.join(changes)}"
            )
        else:
            print(
                f"  ↻  Student row already exists "
                f"(student_id={existing.student_id})"
            )
        return existing

    student = Student(
        id=uuid.uuid4(),
        user_id=user.id,
        student_id=TARGET_STUDENT_ID,
        full_name=f"{user.first_name} {user.last_name}",
        current_semester=1,
        department=TARGET_DEPARTMENT,
        sponsorship_type=TARGET_SPONSORSHIP,
        enrollment_status=EnrollmentStatus.ACTIVE,
    )
    db.add(student)
    await db.flush()
    print(f"  ✅ Created student: {student.student_id}")
    return student


async def _force_password(db: AsyncSession, user: User) -> None:
    """Idempotent: always reset password to pass1234 + clear lockout flag."""
    user.hashed_password = hash_password(TARGET_PASSWORD)
    user.must_change_password = False
    await db.flush()
    print(f"  ✅ Forced password reset (must_change_password=False)")


async def main() -> None:
    print(f"Bootstrapping account for {TARGET_EMAIL} ...")
    async with AsyncSessionLocal() as db:  # type: AsyncSession
        term = await _get_or_create_admission_term(db)
        user = await _get_or_create_user(db)
        app_row = await _get_or_create_application(db, user, term)
        await _get_or_create_enrollment(db, user, app_row, term)
        await _get_or_create_student(db, user)
        await _force_password(db, user)
        await db.commit()

    print()
    print("─" * 58)
    print("DONE — portal credentials:")
    print(f"  Email:      {TARGET_EMAIL}")
    print(f"  UGR ID:     {TARGET_STUDENT_ID}")
    print(f"  Password:   {TARGET_PASSWORD}")
    print("─" * 58)
    print("Login at /api/v1/auth/login with EITHER the email OR the UGR ID.")


if __name__ == "__main__":
    asyncio.run(main())
