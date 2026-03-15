"""
Core shared kernel utilities for the Agentic Registrar backend.

This package intentionally stays free of service-specific business logic and
focuses on cross-cutting concerns such as configuration, logging, error
handling, persistence primitives, and small utilities.
"""

from . import api, config, correlation, db, exceptions, health, logging, security, utils

__all__ = [
    "api",
    "config",
    "correlation",
    "db",
    "exceptions",
    "health",
    "logging",
    "security",
    "utils",
]

