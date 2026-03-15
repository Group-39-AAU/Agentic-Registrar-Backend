from __future__ import annotations

from fastapi import FastAPI

from shared_kernel.correlation import CorrelationIdMiddleware
from shared_kernel.logging import configure_logging

from .api.rest.health import router as health_router
from .api.rest.registrations import router as registrations_router


def create_app() -> FastAPI:
    configure_logging()

    app = FastAPI(title="Agentic Registrar Course Management Service", version="0.1.0")
    app.add_middleware(CorrelationIdMiddleware)

    app.include_router(health_router)
    app.include_router(registrations_router)

    return app


app = create_app()

