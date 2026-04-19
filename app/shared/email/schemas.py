"""
Schemas for reusable outbound email messages.
"""

from pydantic import BaseModel, EmailStr, Field
from pydantic import model_validator


class EmailMessage(BaseModel):
    """Generic outbound email payload shared across modules."""

    to_email: EmailStr
    subject: str = Field(..., min_length=1, max_length=255)
    html_body: str | None = None
    text_body: str | None = None

    @model_validator(mode="after")
    def validate_body_present(self) -> "EmailMessage":
        if not self.html_body and not self.text_body:
            raise ValueError("Either html_body or text_body must be provided")
        return self

