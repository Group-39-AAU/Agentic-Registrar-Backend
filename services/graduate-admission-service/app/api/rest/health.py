from __future__ import annotations

from fastapi import APIRouter

from shared_kernel.health import make_health_status

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    status = make_health_status(status="ok", details={"service": "graduate-admission-service"})
    return status.model_dump()

