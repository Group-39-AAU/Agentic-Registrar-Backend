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

from app.core.config import settings


def create_app() -> FastAPI:
    """Application factory — builds and returns a configured FastAPI instance."""

    app = FastAPI(
        title=settings.APP_NAME,
        description="AI-powered university registrar automation system",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # TODO: Add CORS middleware
    # TODO: Register exception handlers
    # TODO: Mount module routers under settings.API_V1_PREFIX

    @app.get("/health", tags=["System"])
    async def health_check():
        return {"status": "healthy", "environment": settings.ENVIRONMENT}

    return app


app = create_app()
