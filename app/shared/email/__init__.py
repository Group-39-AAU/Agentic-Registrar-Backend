"""Shared email abstractions and services."""

from app.shared.email.schemas import EmailMessage
from app.shared.email.service import EmailService
from app.shared.email.templates import build_uat_acceptance_email, build_welcome_email

__all__ = ["EmailMessage", "EmailService", "build_welcome_email", "build_uat_acceptance_email"]

