from __future__ import annotations

from fastapi import FastAPI

from shared_kernel.correlation import CorrelationIdMiddleware
from shared_kernel.logging import configure_logging

from .api.rest.health import router as health_router
from .api.rest.notifications import router as notifications_router
from .config.settings import get_settings
from .domain import models  # noqa: F401
from .infrastructure.db import Engine


def create_app() -> FastAPI:
    configure_logging()
    _ = get_settings()

    app = FastAPI(title="Agentic Registrar Notification Service", version="0.1.0")
    app.add_middleware(CorrelationIdMiddleware)

    app.include_router(health_router)
    app.include_router(notifications_router)

    @app.on_event("startup")
    def on_startup() -> None:
    return app


app = create_app()

