"""
Agentic Registrar Backend — Application Entry Point.

TODO: Implement:
    - lifespan(app) async context manager
        - on_startup: setup_logging, log app name + environment
        - on_shutdown: log shutdown
    - create_app() -> FastAPI
        - Create FastAPI instance with title, description, docs_url
        - Add CORS middleware
        - Register exception handlers
        - Mount module routers under /api/v1:
            - /api/v1/auth
            - /api/v1/undergraduate
            - /api/v1/graduate
            - /api/v1/courses
        - Add GET /health endpoint
        - Return app instance
    - app = create_app()
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings

# ── Model Registry ─────────────────────────────────────────────
# REQUIRED: SQLAlchemy needs every model class imported and registered
# in Base.metadata before any FK resolution happens at runtime.
# Without these, you get: NoReferencedTableError on the first request.
from app.modules.auth.models import User                          # noqa: F401
from app.modules.programs.models import AcademicProgram           # noqa: F401
from app.shared.audit.models import SystemAuditLog                # noqa: F401
from app.modules.undergraduate.models import (                    # noqa: F401
    UndergraduateApplication, ApplicationDocument,
    ApplicationStatusHistory, RegistrarDecision,
)
from app.ai.models import AIEvaluation, AIExecutionTrace           # noqa: F401

from app.modules.undergraduate.router import router as undergraduate_router


def create_app() -> FastAPI:
    """Application factory — builds and returns a configured FastAPI instance."""

    app = FastAPI(
        title=settings.APP_NAME,
        description="AI-powered university registrar automation system",
        version="0.1.0",
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
    app.include_router(undergraduate_router, prefix=settings.API_V1_PREFIX)
    # TODO: app.include_router(auth_router, prefix=settings.API_V1_PREFIX)

    @app.get("/health", tags=["System"])
    async def health_check():
        return {"status": "healthy", "environment": settings.ENVIRONMENT}

    return app


app = create_app()
