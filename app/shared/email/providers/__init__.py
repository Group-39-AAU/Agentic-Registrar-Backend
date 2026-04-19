"""Email provider implementations."""

from app.shared.email.providers.base import EmailProvider
from app.shared.email.providers.brevo_provider import BrevoProvider

__all__ = ["EmailProvider", "BrevoProvider"]

