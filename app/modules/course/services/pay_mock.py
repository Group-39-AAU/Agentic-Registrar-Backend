"""
PayMock — in-process payment-gateway mock per SDS Table 87.

Realises the design constraint from SRS §2.4 ("Mock Environment
Constraint") and SRS §3.6 (the system shall interface exclusively
with mocked external APIs for payment gateways during development).

Design:
    - The class is instantiable so tests can construct isolated
      instances and seed deterministic state without polluting the
      app-wide singleton.
    - A module-level :data:`pay_mock` singleton is exported for the
      Curriculum Compliance Agent and the Enrollment Adjustment
      Agent to share the same view of payment status across requests.
    - The keying tuple is (student_id, course_id) so government
      cost-sharing forms (one-flag-per-course) and self-sponsored
      per-credit payments are both representable.

Future Phase-2 work: replace this module with an HTTP-talking
adapter that hits the real bursar's payment service. The public
contract — :meth:`get_payment_status` — does not need to change.
"""

from __future__ import annotations

import uuid
from typing import Optional


class PayMock:
    """
    In-memory map of (student_id, course_id) -> paid? booleans.

    Public surface mirrors the SDS Table 87 ``getPaymentStatus``
    operation. The :meth:`set_payment_status`, :meth:`reset`, and
    :meth:`bulk_load` helpers are test/seed conveniences and are
    NOT exposed to application service code.
    """

    def __init__(
        self,
        initial_status_map: Optional[dict[tuple[uuid.UUID, uuid.UUID], bool]] = None,
    ) -> None:
        self._payment_status_map: dict[tuple[uuid.UUID, uuid.UUID], bool] = (
            dict(initial_status_map) if initial_status_map else {}
        )

    # ── Public contract (SDS Table 89 — getPaymentStatus) ────────

    def get_payment_status(
        self, student_id: uuid.UUID, course_id: uuid.UUID,
    ) -> bool:
        """
        Returns True if the student has paid for the specific course;
        otherwise returns False. Unknown (student, course) pairs are
        treated as unpaid — the agent must explicitly mark a payment
        before it counts as settled.
        """
        return self._payment_status_map.get((student_id, course_id), False)

    # ── Test / seed helpers (NOT for application code) ───────────

    def set_payment_status(
        self,
        student_id: uuid.UUID,
        course_id: uuid.UUID,
        *,
        paid: bool,
    ) -> None:
        """Idempotently set the paid flag for a (student, course) pair."""
        self._payment_status_map[(student_id, course_id)] = paid

    def bulk_load(
        self,
        entries: dict[tuple[uuid.UUID, uuid.UUID], bool],
    ) -> None:
        """Replace many entries at once. Used by the seed script."""
        self._payment_status_map.update(entries)

    def reset(self) -> None:
        """Wipe all entries. Tests call this in fixture teardown."""
        self._payment_status_map.clear()

    @property
    def entry_count(self) -> int:
        """
        Number of (student, course) entries currently held. Exposed
        as a property rather than ``__len__`` because making PayMock
        sized would render an empty instance falsy — and several
        agent constructors use ``payment_service or pay_mock`` as a
        fallback, which would silently route empty test PayMocks to
        the module-level singleton.
        """
        return len(self._payment_status_map)


# Module-level singleton shared across the application. Tests that
# need isolated state should construct their own PayMock instance
# rather than mutating this one.
pay_mock = PayMock()
