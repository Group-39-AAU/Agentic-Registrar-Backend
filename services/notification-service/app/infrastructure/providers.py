from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol

from ..domain.schemas import Channel


class NotificationProvider(ABC):
    """Abstract notification provider interface."""

    @abstractmethod
    def send(
        self,
        *,
        channel: Channel,
        recipient: str,
        subject: str | None,
        body: str,
    ) -> None:
        """Send a notification or raise an exception on failure."""


class MockEmailProvider(NotificationProvider):
    def send(
        self,
        *,
        channel: Channel,
        recipient: str,
        subject: str | None,
        body: str,
    ) -> None:
        # In a real implementation, integrate with an email service.
        return None


class MockSmsProvider(NotificationProvider):
    def send(
        self,
        *,
        channel: Channel,
        recipient: str,
        subject: str | None,
        body: str,
    ) -> None:
        # In a real implementation, integrate with an SMS service.
        return None


def get_provider(channel: Channel) -> NotificationProvider:
    if channel == Channel.EMAIL:
        return MockEmailProvider()
    if channel == Channel.SMS:
        return MockSmsProvider()
    # Fallback to email provider by default
    return MockEmailProvider()

