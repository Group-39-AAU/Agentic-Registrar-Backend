"""
Reusable email template builders for all modules.
"""

from pydantic import EmailStr

from app.shared.email.schemas import EmailMessage


def build_welcome_email(to_email: EmailStr, first_name: str) -> EmailMessage:
    """Build the default welcome email for newly registered users."""
    return EmailMessage(
        to_email=to_email,
        subject="Welcome to Agentic Registrar",
        html_body=(
            f"<p>Hi {first_name},</p>"
            "<p>Your account was created successfully.</p>"
        ),
        text_body=(
            f"Hi {first_name},\n\n"
            "Your account was created successfully."
        ),
    )

