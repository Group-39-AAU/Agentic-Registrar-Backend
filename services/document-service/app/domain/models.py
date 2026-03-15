from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import BigInteger, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared_kernel.db import Base, TimestampMixin


class OwnerType(StrEnum):
    USER = "user"
    APPLICATION = "application"
    OTHER = "other"


class UploadStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class VerificationStatus(StrEnum):
    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"


class Document(Base, TimestampMixin):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("storage_path", name="uq_documents_storage_path"),
        {"schema": "documents"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    owner_type: Mapped[OwnerType] = mapped_column(String(32), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    document_type: Mapped[str] = mapped_column(String(64), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    upload_status: Mapped[UploadStatus] = mapped_column(
        String(32),
        nullable=False,
        default=UploadStatus.PENDING,
    )
    verification_status: Mapped[VerificationStatus] = mapped_column(
        String(32),
        nullable=False,
        default=VerificationStatus.PENDING,
    )

