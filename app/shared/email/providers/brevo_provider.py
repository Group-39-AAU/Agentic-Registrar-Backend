"""
Brevo provider implementation.
"""

import httpx

from app.shared.email.providers.base import EmailProvider
from app.shared.email.schemas import EmailMessage


class BrevoProvider(EmailProvider):
    """Send email messages through Brevo Transactional Email API."""

    def __init__(
        self,
        api_key: str,
        from_email: str,
        from_name: str | None = None,
        timeout_seconds: int = 15,
    ) -> None:
        self._api_key = api_key
        self._from_email = from_email
        self._from_name = from_name
        self._timeout_seconds = timeout_seconds

    async def send(self, message: EmailMessage) -> None:
        sender = {"email": self._from_email}
        if self._from_name:
            sender["name"] = self._from_name

        payload: dict[str, object] = {
            "sender": sender,
            "to": [{"email": str(message.to_email)}],
            "subject": message.subject,
        }
        if message.html_body:
            payload["htmlContent"] = message.html_body
        if message.text_body:
            payload["textContent"] = message.text_body

        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "api-key": self._api_key,
        }

        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            response = await client.post(
                "https://api.brevo.com/v3/smtp/email",
                headers=headers,
                json=payload,
            )

        if response.status_code >= 400:
            raise RuntimeError(
                f"Brevo delivery failed with status code {response.status_code}: {response.text}"
            )

