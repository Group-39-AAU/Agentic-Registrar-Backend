from __future__ import annotations

from abc import ABC, abstractmethod


class PaymentProvider(ABC):
    @abstractmethod
    def verify_payment(self, application_id: str) -> bool:
        ...


class GATResultProvider(ABC):
    @abstractmethod
    def fetch_gat_score(self, applicant_id: str) -> float | None:
        ...


class TranscriptVerificationProvider(ABC):
    @abstractmethod
    def verify_transcript(self, application_id: str) -> bool:
        ...


class DocumentServiceClient(ABC):
    @abstractmethod
    def link_document(self, document_id: str) -> None:
        ...


class NotificationServiceClient(ABC):
    @abstractmethod
    def send_notification(self, recipient: str, subject: str, body: str) -> None:
        ...


class MockPaymentProvider(PaymentProvider):
    def verify_payment(self, application_id: str) -> bool:
        return True


class MockGATResultProvider(GATResultProvider):
    def fetch_gat_score(self, applicant_id: str) -> float | None:
        return 70.0


class MockTranscriptVerificationProvider(TranscriptVerificationProvider):
    def verify_transcript(self, application_id: str) -> bool:
        return True


class MockDocumentServiceClient(DocumentServiceClient):
    def link_document(self, document_id: str) -> None:
        return None


class MockNotificationServiceClient(NotificationServiceClient):
    def send_notification(self, recipient: str, subject: str, body: str) -> None:
        return None


def get_payment_provider() -> PaymentProvider:
    return MockPaymentProvider()


def get_gat_provider() -> GATResultProvider:
    return MockGATResultProvider()


def get_transcript_verification_provider() -> TranscriptVerificationProvider:
    return MockTranscriptVerificationProvider()


def get_document_service_client() -> DocumentServiceClient:
    return MockDocumentServiceClient()


def get_notification_service_client() -> NotificationServiceClient:
    return MockNotificationServiceClient()

