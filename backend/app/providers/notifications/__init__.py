"""Notification provider selection.

Three modes behind one Protocol: `console`, `mailtrap` and `gmail`. The last two are both
SMTP and share an implementation; they differ only in configuration.

**The allowlist is the load-bearing rule.** A real send goes only to an address in
`DEMO_RECIPIENT_ALLOWLIST`. Every other recipient gets a `notification` row with
`delivery_mode=simulated`. Three real emails and 601 simulated is honest; implying all 604
were delivered is not — and the 604 passengers are synthetic `@example.com` addresses that
must never be dialled or mailed.

Owner: Stream C.
"""

from __future__ import annotations

from app.config import NotificationMode, get_settings
from app.providers.base import NotificationProvider
from app.providers.notifications.console import ConsoleNotificationProvider
from app.providers.notifications.smtp import SMTPNotificationProvider

__all__ = [
    "ConsoleNotificationProvider",
    "SMTPNotificationProvider",
    "get_notification_provider",
]


def get_notification_provider(mode: NotificationMode | None = None) -> NotificationProvider:
    """Return the configured implementation.

    Resolution is delegated to `app.config.resolve_modes`, which already degrades SMTP to
    console when credentials are missing and reports that degradation. This function must not
    re-implement that decision: two places deciding whether real email is enabled is how a
    demo ends up mailing synthetic addresses.
    """
    settings = get_settings()
    resolved = mode if mode is not None else settings.notification_mode

    if resolved is NotificationMode.console:
        return ConsoleNotificationProvider(allowlist=settings.recipient_allowlist)
    if resolved in {NotificationMode.mailtrap, NotificationMode.gmail}:
        return SMTPNotificationProvider(
            mode=resolved.value,
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            allowlist=settings.recipient_allowlist,
        )

    raise ValueError(f"unknown notification mode: {resolved!r}")
