"""
Base provider contracts for pluggable email vendors.
"""

from typing import Protocol

from app.shared.email.schemas import EmailMessage


class EmailProvider(Protocol):
    """Provider contract implemented by concrete email vendors."""

    async def send(self, message: EmailMessage) -> None:
        """Deliver an email message or raise on failure."""
        ...

