"""Console notification provider: the default, and the one the demo runs on.

Nothing leaves the process. Allowlisted recipients are still reported as `real` **only** if
the caller has explicitly enabled that, because in console mode nothing is actually
delivered — see `deliver_allowlisted`. The default is that every console delivery is
`simulated`, which is the truthful statement for a provider that writes to a log.

Owner: Stream C.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

from app.models.enums import DeliveryMode
from app.providers.base import (
    NotificationRequest,
    NotificationResult,
    ProviderHealth,
)
from app.providers.notifications.base import AllowlistMixin

logger = structlog.get_logger(__name__)


class ConsoleNotificationProvider(AllowlistMixin):
    name = "console"
    mode = "console"

    def __init__(
        self,
        *,
        allowlist: list[str] | None = None,
        now: datetime | None = None,
    ) -> None:
        super().__init__(allowlist=allowlist)
        #: Frozen clock for tests. Production leaves it None.
        self._now = now
        #: Everything written, for assertions and for the demo's "what would have been sent".
        self.sent: list[NotificationRequest] = []

    def _clock(self) -> datetime:
        return self._now or datetime.now(tz=UTC)

    async def health(self) -> ProviderHealth:
        """Never raises. A console writer cannot be down."""
        return ProviderHealth(
            provider=self.name,
            mode=self.mode,
            healthy=True,
            detail=f"{len(self._allowlist)} allowlisted recipients",
            checked_at=self._clock(),
        )

    async def prepare(self, request: NotificationRequest) -> NotificationRequest:
        """Rendering happens in the Communication service; nothing to add here."""
        return request

    async def send_allowlisted(self, request: NotificationRequest) -> NotificationResult:
        """Log the message.

        Reported as `simulated` even for an allowlisted recipient, because a console provider
        delivers nothing. Claiming `real` here would put a false count in the provenance
        ledger — which is precisely what `delivery_mode` exists to prevent.
        """
        self.sent.append(request)
        logger.info(
            "notification.console",
            passenger_id=request.passenger_id,
            recipient=request.recipient,
            channel=request.channel,
            subject=request.subject,
            allowlisted=self.is_allowlisted(request.recipient),
            delivery_mode=DeliveryMode.simulated.value,
        )
        return self.simulated_result(
            request,
            reason="console mode delivers nothing",
            now=self._clock(),
        )

    async def record_simulated_bulk(
        self, requests: list[NotificationRequest]
    ) -> list[NotificationResult]:
        now = self._clock()
        results: list[NotificationResult] = []
        for request in requests:
            self.sent.append(request)
            results.append(self.simulated_result(request, now=now))
        logger.info("notification.console.bulk", count=len(requests))
        return results
