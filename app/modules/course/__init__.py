"""
Course Management module.

Owns the academic lifecycle that begins after a student has been
admitted: course registration, scheduling, grading, academic standing,
and official record generation.

Phase 0 (this commit set) ships the shared catalog only — no routes
are mounted yet. Tracks A, B, and C add their own routers in
subsequent PRs.

Source-of-truth specifications:
    SRS  §3.2.3 Course-FR-01 .. Course-FR-13
    SRS  §3.3   UC-CM-01 .. UC-CM-13
    SRS  §3.5   Inverse Requirements (prerequisite-override gating)
    SRS  §3.7   Logical Database Requirements (RAG knowledge base)
    SDS  §3.1.3 Course Management Class Diagram (Figure 5)
    SDS  §3.2.3 Course Management Sequence Diagrams (Figures 25–37)
    SDS  §3.3   Course Management Lifecycle (Figure 39)
    SDS  §5.3   Detailed Design (Tables 55–84)

Layered structure (mirrors the undergraduate module):
    models.py      — SQLAlchemy entities
    schemas.py     — Pydantic request/response shapes
    repository.py  — Async CRUD
    service.py     — Business logic + audit logging
    router.py      — API endpoints (mounted by Tracks A/B/C)
    agents/        — Concrete agents extending CourseBaseAgent

Phase 0 entities (this package):
    AcademicTerm
    Course, CoursePrerequisite
    Section, ClassScheduleSlot
    Student
    Instructor, InstructorAssignment
    CourseManagementOfficer

Phase 0 enums (in app.shared.enums):
    RegistrationStatus    AcademicStatusType    EnrollmentStatus
    GradeLetter           GradeSubmissionStatus AddDropAction
    ExceptionStatus       AgentStatus
    OfficerRole           RiskStatus

Three tracks own the workflow tables that land on top of this base:
    Track A — Curriculum Compliance, Academic Scheduling,
              Enrollment Adjustment, Academic Advisory
    Track B — Assessment Validation, Grade Monitoring,
              Grade Authorization Facilitator
    Track C — Academic Standing, Academic Records,
              Exception Resolution

Hard rules (carried into every concrete agent):
    - Every state change writes a system_audit_logs row.
    - Every irreversible decision (final grade, status, dismissal)
      passes through an explicit CourseManagementOfficer click.
    - Prerequisite override is gated to OfficerRole.DEPARTMENT_HEAD
      per SRS §3.5 Inverse Requirements.
    - The 22-ECTS credit ceiling and 12-ECTS floor (SDS Tables 65, 80)
      are not bypassable by agents — only by Department Head override.
"""
