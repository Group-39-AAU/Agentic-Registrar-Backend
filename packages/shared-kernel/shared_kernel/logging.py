from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from typing import Any

from .correlation import get_correlation_id


def _default_formatter() -> logging.Formatter:
    """Create a simple structured log formatter suitable for production."""

    class StructuredFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
            timestamp = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()
            correlation_id = get_correlation_id()
            base: dict[str, Any] = {
                "ts": timestamp,
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            if correlation_id is not None:
                base["correlation_id"] = correlation_id
            if record.exc_info:
                base["exc_info"] = self.formatException(record.exc_info)
            # Key=value style for easy log parsing
            return " ".join(f"{k}={v!r}" for k, v in base.items())

    return StructuredFormatter()


_configured = False


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging with a structured formatter.

    Idempotent: can be safely called multiple times without adding duplicate handlers.
    """

    global _configured
    if _configured:
        return

    root = logging.getLogger()
    root.setLevel(level.upper())

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_default_formatter())
    root.handlers.clear()
    root.addHandler(handler)

    _configured = True


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a module- or service-level logger."""

    return logging.getLogger(name)

