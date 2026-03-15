from __future__ import annotations

from typing import Mapping

from .api import HealthStatus


def make_health_status(
    *,
    status: str = "ok",
    details: Mapping[str, object] | None = None,
) -> HealthStatus:
    """Create a `HealthStatus` object from simple inputs."""

    return HealthStatus(status=status, details=dict(details or {}))

