"""
Reusable email service facade for application modules.
"""

from app.core.logging import get_logger
from app.shared.email.providers.base import EmailProvider
from app.shared.email.schemas import EmailMessage

logger = get_logger("shared.email")


class EmailService:
    """
    Cross-module email service.

    Keeps modules decoupled from vendor SDK details by depending on
    this service rather than calling a provider SDK directly.
    """

    def __init__(self, provider: EmailProvider | None, enabled: bool) -> None:
        self._provider = provider
        self._enabled = enabled

    async def send(self, message: EmailMessage) -> bool:
        """
        Send an email if enabled and configured.
        Returns True when delivery request is accepted by provider.
        """
        if not self._enabled:
            logger.info(
                "Email disabled; skipped send to=%s subject=%s",
                message.to_email,
                message.subject,
            )
            return False

        if self._provider is None:
            logger.warning(
                "Email provider not configured; skipped send to=%s subject=%s",
                message.to_email,
                message.subject,
            )
            return False
        await self._provider.send(message)
        logger.info("Email sent to=%s subject=%s", message.to_email, message.subject)
        return True

