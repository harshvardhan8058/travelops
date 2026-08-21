"""Shared allowlist and record-building for every notification provider.

The allowlist logic lives here rather than in each implementation because it is the one thing
that must behave identically in all of them. A console provider that accidentally reported
`delivery_mode=real`, or an SMTP provider that mailed a synthetic address, would both be
serious — the first misleads a reviewer, the second mails 604 `@example.com` addresses.

Owner: Stream C.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.models.enums import DeliveryMode
from app.providers.base import NotificationRequest, NotificationResult


def normalise_recipient(recipient: str) -> str:
    return recipient.strip().lower()


class AllowlistMixin:
    """Decides `real` versus `simulated`, and nothing else."""

    name: str

    def __init__(self, *, allowlist: list[str] | None = None) -> None:
        self._allowlist = {normalise_recipient(item) for item in (allowlist or []) if item.strip()}

    @property
    def allowlist(self) -> set[str]:
        return set(self._allowlist)

    def is_allowlisted(self, recipient: str) -> bool:
        """Exact match only.

        No domain wildcards, no suffix matching. `@example.com` must never become
        deliverable by accident, and a rule permissive enough to be convenient is permissive
        enough to mail 604 synthetic addresses.
        """
        return normalise_recipient(recipient) in self._allowlist

    def simulated_result(
        self,
        request: NotificationRequest,
        *,
        reason: str = "recipient not in DEMO_RECIPIENT_ALLOWLIST",
        now: datetime | None = None,
    ) -> NotificationResult:
        return NotificationResult(
            passenger_id=request.passenger_id,
            channel=request.channel,
            delivery_mode=DeliveryMode.simulated.value,
            status="sent",
            provider_message_id=f"simulated:{self.name}:{request.passenger_id}:{reason}",
            sent_at=now or datetime.now(tz=UTC),
        )
