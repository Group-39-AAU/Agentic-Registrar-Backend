"""
Agentic Registrar Backend — Application Entry Point.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings

# ── Model Registry ─────────────────────────────────────────────
# REQUIRED: SQLAlchemy needs every model class imported and registered
# in Base.metadata before any FK resolution happens at runtime.
from app.modules.auth.models import User                          # noqa: F401
from app.modules.programs.models import AcademicProgram           # noqa: F401
from app.shared.audit.models import SystemAuditLog                # noqa: F401
from app.modules.undergraduate.models import (                    # noqa: F401
    UndergraduateAdmissionTerm,
    UndergraduateApplication, ApplicationDocument,
    ApplicationStatusHistory, RegistrarDecision,
)
from app.ai.models import AIEvaluation, AIExecutionTrace           # noqa: F401
from app.modules.moe.models import MoeStudentRecord                # noqa: F401
from app.modules.testing_center.models import UATRecord            # noqa: F401
from app.modules.undergraduate.ranking.models import StreamQuota, RankingResult  # noqa: F401
from app.modules.undergraduate.enrollment.models import Enrollment                # noqa: F401
from app.modules.course.models import (                                           # noqa: F401
    AcademicTerm, Course, CoursePrerequisite, Section, ClassScheduleSlot,
    Classroom,
    Student, Instructor, InstructorAssignment, CourseManagementOfficer,
    Registration, RegistrationCourse, RegistrationStatusHistory,
    AddDropRequest, AdvisoryRecommendation, PrerequisiteOverride,
    ScheduleConflict,
)

from app.modules.auth.router import router as auth_router
from app.modules.programs.router import router as programs_router
from app.modules.undergraduate.router import router as undergraduate_router
from app.modules.moe.router import router as moe_router
from app.modules.testing_center.router import router as testing_center_router
from app.modules.undergraduate.ranking.router import router as ranking_router
from app.modules.undergraduate.ranking.review_router import router as review_router
from app.modules.undergraduate.enrollment.router import router as enrollment_router
from app.modules.course.router import router as course_router
from app.modules.course.grading.router import router as grading_router


def create_app() -> FastAPI:
    """Application factory — builds and returns a configured FastAPI instance."""

    app = FastAPI(
        title=settings.APP_NAME,
        description="AI-powered university registrar automation system",
        version="0.2.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # ── CORS ──
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Module Routers ──
    app.include_router(auth_router, prefix=settings.API_V1_PREFIX)
    app.include_router(programs_router, prefix=settings.API_V1_PREFIX)
    app.include_router(undergraduate_router, prefix=settings.API_V1_PREFIX)
    app.include_router(moe_router, prefix=settings.API_V1_PREFIX)
    app.include_router(testing_center_router, prefix=settings.API_V1_PREFIX)
    app.include_router(ranking_router, prefix=settings.API_V1_PREFIX)
    app.include_router(review_router, prefix=settings.API_V1_PREFIX)
    app.include_router(enrollment_router, prefix=settings.API_V1_PREFIX)
    app.include_router(course_router, prefix=settings.API_V1_PREFIX)
    app.include_router(grading_router, prefix=settings.API_V1_PREFIX)

    # ── Event Subscriptions ──
    from app.shared.events import subscribe
    from app.modules.undergraduate.event_handlers import handle_uat_completed
    subscribe("UATCompletedEvent", handle_uat_completed)

    @app.get("/health", tags=["System"])
    async def health_check():
        return {"status": "healthy", "environment": settings.ENVIRONMENT}

    return app


app = create_app()
