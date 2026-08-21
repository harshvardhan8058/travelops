"""Communication service — STREAM C.

Render approved templates and dispatch through the notification provider.

Real sends go ONLY to the configured allowlist. Every other recipient produces a
notification row with delivery_mode=simulated. Three real emails and 601 simulated is
honest; implying all 604 were delivered is not.

## Two rules that make this service safe

1. **Templates are approved data, not code.** They live in `fixtures/notifications/
   templates.json` with a `review_status`, and a template that is not `approved` is refused.
   Substitution is a literal placeholder replacement — no expression evaluation, no template
   engine — so a passenger-supplied value can never execute anything.
2. **A missing fact is a refusal, not a blank.** Rendering `Dear ,` or `delayed by None
   minutes` and sending it to a real inbox is worse than sending nothing, so an incomplete
   context returns `needs_human` and the message is not dispatched.

The counts this service returns are the honest record: `real_count` is only ever the number of
allowlisted recipients actually delivered to.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.enums import ActionStatus, DeliveryMode, ProvenanceKind
from app.providers.base import NotificationProvider, NotificationRequest, ProviderError
from app.services.base import ServiceResult

RULE_VERSION = "communication-v1"

#: backend/app/services/communication.py -> parents[3] is the repo root locally and `/`
#: inside the container, where ./fixtures is mounted at /fixtures. Mirrors fixtures_router.
TEMPLATE_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "notifications"
TEMPLATE_FILE = TEMPLATE_DIR / "templates.json"

APPROVED_STATUS = "approved"

_PLACEHOLDER = re.compile(r"\{([a-z0-9_]+)\}")


class TemplateNotApprovedError(RuntimeError):
    """Raised when a template exists but has not been reviewed."""


class MissingTemplateFactsError(RuntimeError):
    def __init__(self, template_id: str, missing: list[str]) -> None:
        super().__init__(f"{template_id} is missing required facts: {', '.join(missing)}")
        self.template_id = template_id
        self.missing = missing


@dataclass(frozen=True, slots=True)
class Template:
    template_id: str
    version: str
    channel: str
    review_status: str
    subject: str
    body: str
    required_facts: tuple[str, ...]

    def render(self, facts: dict[str, Any]) -> tuple[str, str]:
        if self.review_status != APPROVED_STATUS:
            raise TemplateNotApprovedError(
                f"{self.template_id} has review_status={self.review_status!r}; only "
                f"{APPROVED_STATUS!r} templates may be dispatched"
            )

        missing = [
            fact
            for fact in self.required_facts
            if facts.get(fact) is None or str(facts.get(fact)).strip() == ""
        ]
        if missing:
            raise MissingTemplateFactsError(self.template_id, missing)

        def substitute(text: str) -> str:
            return _PLACEHOLDER.sub(lambda match: str(facts[match.group(1)]), text)

        return substitute(self.subject), substitute(self.body)

    def placeholders(self) -> set[str]:
        return set(_PLACEHOLDER.findall(self.subject)) | set(_PLACEHOLDER.findall(self.body))


def load_templates(path: Path = TEMPLATE_FILE) -> dict[str, Template]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        entry["template_id"]: Template(
            template_id=entry["template_id"],
            version=entry["version"],
            channel=entry["channel"],
            review_status=entry["review_status"],
            subject=entry["subject"],
            body=entry["body"],
            required_facts=tuple(entry["required_facts"]),
        )
        for entry in payload["templates"]
    }


class Recipient(BaseModel):
    """One passenger to contact, with the facts their message needs."""

    model_config = ConfigDict(extra="forbid")

    passenger_id: int
    passenger_reference: str
    email: str
    facts: dict[str, Any]


class CommunicationService:
    name = "communication"

    async def execute(self, **kwargs: Any) -> ServiceResult:
        """Render and dispatch.

        Inputs:
            template_id: str
            recipients:  list[Recipient]
            provider:    NotificationProvider
            channel:     optional override, defaults to the template's channel
        """
        template_id = kwargs.get("template_id")
        recipients_raw = kwargs.get("recipients")
        provider: NotificationProvider | None = kwargs.get("provider")

        missing_inputs = [
            name
            for name, value in (
                ("template_id", template_id),
                ("recipients", recipients_raw),
                ("provider", provider),
            )
            if value is None
        ]
        if missing_inputs:
            return ServiceResult(
                status=ActionStatus.needs_human,
                reason=(
                    "Communication needs a template, recipients and a provider. Missing: "
                    f"{', '.join(missing_inputs)}"
                ),
                payload={"rule_version": RULE_VERSION},
                provenance_kind=ProvenanceKind.unavailable.value,
            )

        assert provider is not None
        recipients = [
            Recipient.model_validate(item) if isinstance(item, dict) else item
            for item in recipients_raw or []
        ]

        templates = load_templates(kwargs.get("template_path") or TEMPLATE_FILE)
        template = templates.get(str(template_id))
        if template is None:
            return ServiceResult(
                status=ActionStatus.needs_human,
                reason=(
                    f"No approved template '{template_id}'. Available: "
                    f"{', '.join(sorted(templates))}"
                ),
                payload={"rule_version": RULE_VERSION},
                provenance_kind=ProvenanceKind.unavailable.value,
            )

        channel = str(kwargs.get("channel") or template.channel)

        prepared: list[tuple[Recipient, NotificationRequest]] = []
        unrenderable: list[dict[str, Any]] = []

        for recipient in recipients:
            try:
                subject, body = template.render(recipient.facts)
            except MissingTemplateFactsError as exc:
                # A half-rendered message is worse than none. Recorded, not sent.
                unrenderable.append(
                    {
                        "passenger_reference": recipient.passenger_reference,
                        "missing_facts": exc.missing,
                    }
                )
                continue
            except TemplateNotApprovedError as exc:
                return ServiceResult(
                    status=ActionStatus.needs_human,
                    reason=str(exc),
                    payload={"rule_version": RULE_VERSION, "template_id": template.template_id},
                    provenance_kind=ProvenanceKind.unavailable.value,
                )

            prepared.append(
                (
                    recipient,
                    NotificationRequest(
                        passenger_id=recipient.passenger_id,
                        recipient=recipient.email,
                        channel=channel,
                        subject=subject,
                        body=body,
                    ),
                )
            )

        allowlisted = [
            (recipient, request)
            for recipient, request in prepared
            if _is_allowlisted(provider, request.recipient)
        ]
        bulk = [
            request
            for recipient, request in prepared
            if not _is_allowlisted(provider, request.recipient)
        ]

        results = []
        errors: list[dict[str, str]] = []

        for _recipient, request in allowlisted:
            try:
                results.append(await provider.send_allowlisted(request))
            except ProviderError as exc:
                # Typed, and never mapped to silent success.
                errors.append(
                    {
                        "recipient_passenger_id": str(request.passenger_id),
                        "kind": exc.kind.value,
                        "message": exc.message,
                    }
                )

        if bulk:
            results.extend(await provider.record_simulated_bulk(bulk))

        real_count = sum(1 for result in results if result.delivery_mode == DeliveryMode.real.value)
        simulated_count = sum(
            1 for result in results if result.delivery_mode == DeliveryMode.simulated.value
        )

        evidence = [f"template:{template.template_id}:{template.version}"]
        evidence += [f"passenger:{recipient.passenger_id}" for recipient in recipients]

        status = ActionStatus.success
        if errors or unrenderable:
            # Partial delivery is reported as needing a human rather than rounded up to
            # success. Someone has to decide what happens to the ones that did not go.
            status = ActionStatus.needs_human

        reason = (
            f"{real_count} real and {simulated_count} simulated {channel} messages from "
            f"template {template.template_id}"
        )
        if unrenderable:
            reason += f"; {len(unrenderable)} not rendered for missing facts"
        if errors:
            reason += f"; {len(errors)} provider failures"

        return ServiceResult(
            status=status,
            reason=reason,
            payload={
                "rule_version": RULE_VERSION,
                "template_id": template.template_id,
                "template_version": template.version,
                "channel": channel,
                "recipients_requested": len(recipients),
                "real_count": real_count,
                "simulated_count": simulated_count,
                "not_rendered": unrenderable,
                "provider_errors": errors,
                "notifications": [result.model_dump(mode="json") for result in results],
                "honesty_note": (
                    f"{real_count} message(s) were actually delivered. The remaining "
                    f"{simulated_count} are recorded with delivery_mode=simulated and were "
                    f"not sent anywhere."
                ),
            },
            evidence_refs=sorted(set(evidence)),
            provenance_kind=(
                ProvenanceKind.real.value if real_count else ProvenanceKind.simulated.value
            ),
        )


def _is_allowlisted(provider: NotificationProvider, recipient: str) -> bool:
    """Ask the provider, which owns the allowlist.

    Duplicating the rule here would be a second place that decides whether real email is
    enabled, and the two would eventually disagree.
    """
    checker = getattr(provider, "is_allowlisted", None)
    if checker is None:
        return False
    return bool(checker(recipient))
