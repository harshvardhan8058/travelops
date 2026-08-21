"""SMTP notification provider, covering both `mailtrap` and `gmail` modes.

They are the same protocol and differ only in configuration, so one implementation serves
both and the mode is carried through for the provenance ledger.

**A real send is attempted only for an allowlisted recipient.** Everything else returns a
simulated result without touching the network. That check happens before the connection is
opened, so a bug in the transport cannot mail a synthetic address.

Owner: Stream C.
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from datetime import UTC, datetime
from email.message import EmailMessage

import structlog

from app.models.enums import DeliveryMode
from app.providers.base import (
    NotificationRequest,
    NotificationResult,
    ProviderError,
    ProviderErrorKind,
    ProviderHealth,
)
from app.providers.notifications.base import AllowlistMixin

logger = structlog.get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 10.0


class SMTPNotificationProvider(AllowlistMixin):
    name = "smtp"

    def __init__(
        self,
        *,
        mode: str,
        host: str,
        port: int,
        username: str,
        password: str,
        allowlist: list[str] | None = None,
        sender: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        now: datetime | None = None,
    ) -> None:
        super().__init__(allowlist=allowlist)
        self.mode = mode
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._sender = sender or username
        self._timeout = timeout_seconds
        self._now = now
        self.sent: list[NotificationRequest] = []

    def _clock(self) -> datetime:
        return self._now or datetime.now(tz=UTC)

    async def health(self) -> ProviderHealth:
        """Never raises. Reports missing configuration rather than failing a probe."""
        checked_at = self._clock()
        missing = [
            label
            for label, value in (
                ("SMTP_HOST", self._host),
                ("SMTP_USERNAME", self._username),
                ("SMTP_PASSWORD", self._password),
            )
            if not value
        ]
        if missing:
            return ProviderHealth(
                provider=self.name,
                mode=self.mode,
                healthy=False,
                detail=f"missing configuration: {', '.join(missing)}",
                checked_at=checked_at,
            )
        if not self._allowlist:
            return ProviderHealth(
                provider=self.name,
                mode=self.mode,
                healthy=True,
                detail="configured but DEMO_RECIPIENT_ALLOWLIST is empty; all sends simulated",
                checked_at=checked_at,
            )
        return ProviderHealth(
            provider=self.name,
            mode=self.mode,
            healthy=True,
            detail=f"{len(self._allowlist)} allowlisted recipients",
            checked_at=checked_at,
        )

    async def prepare(self, request: NotificationRequest) -> NotificationRequest:
        return request

    def _build_message(self, request: NotificationRequest) -> EmailMessage:
        message = EmailMessage()
        message["From"] = self._sender or "travelops-demo@localhost"
        message["To"] = request.recipient
        message["Subject"] = request.subject or "TravelOps AI notification"
        message.set_content(request.body)
        return message

    def _send_sync(self, message: EmailMessage) -> str:
        context = ssl.create_default_context()
        with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as client:
            client.starttls(context=context)
            if self._username:
                client.login(self._username, self._password)
            client.send_message(message)
        return str(message.get("Message-ID") or "")

    async def send_allowlisted(self, request: NotificationRequest) -> NotificationResult:
        """Attempt a real send, but only for an allowlisted recipient.

        The allowlist is checked before any connection is opened, so no transport bug can
        reach a synthetic address.
        """
        if not self.is_allowlisted(request.recipient):
            return self.simulated_result(request, now=self._clock())

        message = self._build_message(request)
        try:
            # stdlib rather than anyio: anyio is only a transitive dependency here, and
            # pyproject.toml belongs to Stream A.
            message_id = await asyncio.to_thread(self._send_sync, message)
        except smtplib.SMTPAuthenticationError as exc:
            raise ProviderError(
                ProviderErrorKind.forbidden,
                f"SMTP rejected the credentials: {exc}",
                provider=self.name,
            ) from exc
        except TimeoutError as exc:
            raise ProviderError(
                ProviderErrorKind.timeout,
                f"SMTP did not respond within {self._timeout}s",
                provider=self.name,
            ) from exc
        except (smtplib.SMTPException, OSError) as exc:
            # Never mapped to success: a failed delivery reported as sent is the one outcome
            # that must not happen.
            raise ProviderError(
                ProviderErrorKind.unavailable,
                f"SMTP delivery failed: {exc}",
                provider=self.name,
            ) from exc

        self.sent.append(request)
        logger.info(
            "notification.smtp.sent",
            passenger_id=request.passenger_id,
            channel=request.channel,
            delivery_mode=DeliveryMode.real.value,
        )
        return NotificationResult(
            passenger_id=request.passenger_id,
            channel=request.channel,
            delivery_mode=DeliveryMode.real.value,
            status="sent",
            provider_message_id=message_id or f"smtp:{self.mode}:{request.passenger_id}",
            sent_at=self._clock(),
        )

    async def record_simulated_bulk(
        self, requests: list[NotificationRequest]
    ) -> list[NotificationResult]:
        """No network traffic at all. Bulk is always simulated by design."""
        now = self._clock()
        logger.info("notification.smtp.bulk_simulated", count=len(requests))
        return [self.simulated_result(request, now=now) for request in requests]
