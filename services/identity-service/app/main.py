from __future__ import annotations

from fastapi import FastAPI

from shared_kernel.correlation import CorrelationIdMiddleware
from shared_kernel.logging import configure_logging

from .api.rest.auth import router as auth_router
from .api.rest.health import router as health_router
from .config.settings import get_settings
from .infrastructure.db import Engine
from .application.services import SeedService
from .infrastructure.security import create_password_hasher
from .domain import models  # noqa: F401


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()

    app = FastAPI(title="Agentic Registrar Identity Service", version="0.1.0")

    app.add_middleware(CorrelationIdMiddleware)

    app.include_router(health_router)
    app.include_router(auth_router)

    @app.on_event("startup")
    def on_startup() -> None:
        from sqlalchemy.orm import Session

        from .infrastructure.db import SessionLocal

        with SessionLocal() as session:  # type: Session
            seed = SeedService(session, hasher=create_password_hasher())
            seed.ensure_default_roles()
            if (
                settings.admin_username
                and settings.admin_password
                and settings.admin_email
            ):
                seed.ensure_admin_user(
                    username=settings.admin_username,
                    password=settings.admin_password,
                    email=settings.admin_email,
                )

    return app


app = create_app()

