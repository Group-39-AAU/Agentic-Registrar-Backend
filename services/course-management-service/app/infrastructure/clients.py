from __future__ import annotations

from abc import ABC, abstractmethod


class PaymentProvider(ABC):
    @abstractmethod
    def verify_payment(self, registration_id: str) -> bool: ...


class NotificationServiceClient(ABC):
    @abstractmethod
    def send_notification(self, recipient: str, subject: str, body: str) -> None: ...


class IdentityServiceClient(ABC):
    @abstractmethod
    def get_student_email(self, student_id: str) -> str | None: ...


class MockPaymentProvider(PaymentProvider):
    def verify_payment(self, registration_id: str) -> bool:
        return True


class MockNotificationServiceClient(NotificationServiceClient):
    def send_notification(self, recipient: str, subject: str, body: str) -> None:
        return None


class MockIdentityServiceClient(IdentityServiceClient):
    def get_student_email(self, student_id: str) -> str | None:
        return "student@example.com"


def get_payment_provider() -> PaymentProvider:
    return MockPaymentProvider()


def get_notification_client() -> NotificationServiceClient:
    return MockNotificationServiceClient()


def get_identity_client() -> IdentityServiceClient:
    return MockIdentityServiceClient()

