"""
Reusable email service facade for application modules.
"""

import re

from app.core.logging import get_logger
from app.shared.email.providers.base import EmailProvider
from app.shared.email.schemas import EmailMessage

logger = get_logger("shared.email")


# Seeded test recipients we never want to actually email — the per-day
# provider quota is precious during demos. Each pattern matches an email
# format that ONLY the seed scripts produce; real users (including your
# admission-flow demo applicant) won't collide with these.
#
#   staff-0001-10@aau.edu.et         — instructors  (seed_course_management)
#   ugr-0001-19@aau.edu.et           — students     (seed_course_management)
#   reg-9999-15@aau.edu.et           — legacy Track-B dept head (kept for safety)
#   registrar.officer@aau.edu.et     — default officer (seed_undergraduate_admission + seed_course_management)
#   se.dept.head@aau.edu.et          — SE dept head (seed_course_management)
#   officer@aau.edu.et               — admission seed officer
#   ranking_test_student_NNN@aau.edu.et — admission-seed test applicants
_SEEDED_TEST_RECIPIENT_PATTERNS = [
    re.compile(r"^staff-[0-9]+-[0-9]+@aau\.edu\.et$"),
    re.compile(r"^ugr-[0-9]+-[0-9]+@aau\.edu\.et$"),
    re.compile(r"^reg-[0-9]+-[0-9]+@aau\.edu\.et$"),
    re.compile(r"^registrar\.officer@aau\.edu\.et$"),
    re.compile(r"^se\.dept\.head@aau\.edu\.et$"),
    re.compile(r"^officer@aau\.edu\.et$"),
    re.compile(r"^ranking_test_student_[0-9]+@aau\.edu\.et$"),
]


def _is_seeded_test_recipient(email: str) -> bool:
    """True for any of the addresses created by the seed scripts."""
    if not email:
        return False
    normalized = email.strip().lower()
    return any(p.match(normalized) for p in _SEEDED_TEST_RECIPIENT_PATTERNS)


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

        # Per-day provider quota is limited — never spend it on the
        # synthetic test recipients the seed scripts create.
        if _is_seeded_test_recipient(message.to_email):
            logger.info(
                "Seeded test recipient; skipped send to=%s subject=%s",
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

