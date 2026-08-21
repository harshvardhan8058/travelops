"""Communication: the allowlist, the honesty of the counts, and refusal over half-rendering.

The single most important assertion here is that 604 synthetic `@example.com` recipients
produce 604 simulated records and zero real sends.
"""

from __future__ import annotations

import json

import pytest

from app.models.enums import ActionStatus, DeliveryMode, ProvenanceKind
from app.providers.base import NotificationProvider, ProviderError, ProviderErrorKind
from app.providers.notifications.console import ConsoleNotificationProvider
from app.providers.notifications.smtp import SMTPNotificationProvider
from app.services.communication import (
    RULE_VERSION,
    TEMPLATE_FILE,
    CommunicationService,
    MissingTemplateFactsError,
    Recipient,
    TemplateNotApprovedError,
    load_templates,
)

DELAY_FACTS = {
    "passenger_name": "Aarav Sharma",
    "flight_number": "6E 2134",
    "origin_city": "Bengaluru",
    "destination_city": "Delhi",
    "scheduled_departure_local": "21:10 IST",
    "revised_departure_local": "04:10 IST (+1)",
    "delay_minutes": 420,
    "pnr": "DW58U8",
}


@pytest.fixture
def service() -> CommunicationService:
    return CommunicationService()


def _recipient(number: int, email: str | None = None) -> Recipient:
    reference = f"PAX-{number:05d}"
    return Recipient(
        passenger_id=number,
        passenger_reference=reference,
        email=email or f"{reference.lower()}@example.com",
        facts=dict(DELAY_FACTS),
    )


# ------------------------------------------------------------------------- templates


def test_templates_are_committed_and_approved():
    templates = load_templates()
    assert templates
    for template in templates.values():
        assert template.review_status == "approved"
        assert template.version == "notify-v1"


def test_every_placeholder_is_a_declared_required_fact():
    """A placeholder nobody declared would render as a literal `{brace}` in a real inbox."""
    for template in load_templates().values():
        assert template.placeholders() == set(template.required_facts)


def test_templates_disclose_that_the_system_is_a_demonstration():
    """Anything that could reach a real inbox must not imply a booking changed."""
    for template in load_templates().values():
        assert "demonstration system" in template.body
        assert "no payment has been taken" in template.body


def test_template_file_is_valid_json_on_disk():
    json.loads(TEMPLATE_FILE.read_text(encoding="utf-8"))


def test_rendering_substitutes_every_placeholder():
    template = load_templates()["delay_notice"]
    subject, body = template.render(DELAY_FACTS)
    assert "6E 2134" in subject
    assert "420" in body
    assert "{" not in body


def test_rendering_is_literal_substitution_not_evaluation():
    """A passenger-supplied value must never be executable."""
    template = load_templates()["delay_notice"]
    facts = dict(DELAY_FACTS, passenger_name="{delay_minutes} __import__('os')")
    _subject, body = template.render(facts)
    assert "__import__('os')" in body
    # The injected placeholder is emitted literally, not resolved a second time.
    assert "{delay_minutes} __import__" in body


def test_missing_fact_raises_rather_than_rendering_a_blank():
    template = load_templates()["delay_notice"]
    with pytest.raises(MissingTemplateFactsError) as exc:
        template.render({**DELAY_FACTS, "passenger_name": None})
    assert exc.value.missing == ["passenger_name"]


def test_empty_string_counts_as_missing():
    """`Dear ,` is worse than sending nothing."""
    template = load_templates()["delay_notice"]
    with pytest.raises(MissingTemplateFactsError):
        template.render({**DELAY_FACTS, "passenger_name": "   "})


def test_unapproved_template_is_refused():
    template = load_templates()["delay_notice"]
    draft = type(template)(
        template_id=template.template_id,
        version=template.version,
        channel=template.channel,
        review_status="draft",
        subject=template.subject,
        body=template.body,
        required_facts=template.required_facts,
    )
    with pytest.raises(TemplateNotApprovedError):
        draft.render(DELAY_FACTS)


# --------------------------------------------------------------------- the allowlist


def test_console_provider_satisfies_the_protocol():
    assert isinstance(ConsoleNotificationProvider(), NotificationProvider)


def test_smtp_provider_satisfies_the_protocol():
    provider = SMTPNotificationProvider(
        mode="mailtrap", host="h", port=587, username="u", password="p"
    )
    assert isinstance(provider, NotificationProvider)


@pytest.mark.parametrize(
    ("allowlist", "recipient", "expected"),
    [
        (["ops@skyforge.test"], "ops@skyforge.test", True),
        (["ops@skyforge.test"], "OPS@SkyForge.TEST", True),
        (["ops@skyforge.test"], " ops@skyforge.test ", True),
        (["ops@skyforge.test"], "pax-00001@example.com", False),
        ([], "ops@skyforge.test", False),
        # No suffix or domain matching: a rule permissive enough to be convenient is
        # permissive enough to mail 604 synthetic addresses.
        (["@skyforge.test"], "ops@skyforge.test", False),
        (["skyforge.test"], "ops@skyforge.test", False),
    ],
)
def test_allowlist_is_exact_match_only(allowlist, recipient, expected):
    provider = ConsoleNotificationProvider(allowlist=allowlist)
    assert provider.is_allowlisted(recipient) is expected


async def test_console_never_claims_a_real_delivery():
    """A console provider delivers nothing, so `real` would be a false ledger entry even for
    an allowlisted address."""
    provider = ConsoleNotificationProvider(allowlist=["ops@skyforge.test"])
    from app.providers.base import NotificationRequest

    result = await provider.send_allowlisted(
        NotificationRequest(
            passenger_id=1, recipient="ops@skyforge.test", channel="email", body="hello"
        )
    )
    assert result.delivery_mode == DeliveryMode.simulated.value


async def test_smtp_does_not_open_a_connection_for_a_non_allowlisted_address():
    """Checked before the transport, so no transport bug can reach a synthetic address."""
    provider = SMTPNotificationProvider(
        mode="mailtrap",
        host="127.0.0.1",
        port=1,  # would fail instantly if a connection were attempted
        username="u",
        password="p",
        allowlist=["ops@skyforge.test"],
    )
    from app.providers.base import NotificationRequest

    result = await provider.send_allowlisted(
        NotificationRequest(
            passenger_id=1, recipient="pax-00001@example.com", channel="email", body="hi"
        )
    )
    assert result.delivery_mode == DeliveryMode.simulated.value
    assert provider.sent == []


async def test_smtp_bulk_never_touches_the_network():
    provider = SMTPNotificationProvider(
        mode="mailtrap",
        host="127.0.0.1",
        port=1,
        username="u",
        password="p",
        allowlist=["ops@skyforge.test"],
    )
    from app.providers.base import NotificationRequest

    results = await provider.record_simulated_bulk(
        [
            NotificationRequest(
                passenger_id=n, recipient="ops@skyforge.test", channel="email", body="x"
            )
            for n in range(3)
        ]
    )
    assert all(r.delivery_mode == DeliveryMode.simulated.value for r in results)
    assert provider.sent == []


async def test_smtp_health_reports_missing_configuration_without_raising():
    provider = SMTPNotificationProvider(mode="gmail", host="", port=587, username="", password="")
    health = await provider.health()
    assert health.healthy is False
    assert "SMTP_HOST" in health.detail


async def test_smtp_health_warns_when_the_allowlist_is_empty():
    provider = SMTPNotificationProvider(
        mode="gmail", host="h", port=587, username="u", password="p", allowlist=[]
    )
    health = await provider.health()
    assert health.healthy is True
    assert "all sends simulated" in health.detail


# ---------------------------------------------------------------- the honest counts


async def test_synthetic_passengers_produce_no_real_sends(service):
    """The assertion that keeps the demo honest at scale."""
    provider = ConsoleNotificationProvider(allowlist=["ops@skyforge.test"])
    recipients = [_recipient(n) for n in range(1, 605)]

    result = await service.execute(
        template_id="delay_notice", recipients=recipients, provider=provider
    )
    assert result.status is ActionStatus.success
    assert result.payload["real_count"] == 0
    assert result.payload["simulated_count"] == 604
    assert result.payload["recipients_requested"] == 604


async def test_allowlisted_recipient_is_delivered_and_counted_separately(service):
    """Three real and 601 simulated is the honest record."""

    class DeliveringProvider(ConsoleNotificationProvider):
        """Console provider that does report `real`, standing in for working SMTP."""

        async def send_allowlisted(self, request):
            from datetime import UTC, datetime

            from app.providers.base import NotificationResult

            self.sent.append(request)
            return NotificationResult(
                passenger_id=request.passenger_id,
                channel=request.channel,
                delivery_mode=DeliveryMode.real.value,
                status="sent",
                provider_message_id="test-1",
                sent_at=datetime.now(tz=UTC),
            )

    provider = DeliveringProvider(allowlist=["ops1@skyforge.test", "ops2@skyforge.test"])
    recipients = [
        _recipient(1, "ops1@skyforge.test"),
        _recipient(2, "ops2@skyforge.test"),
        *[_recipient(n) for n in range(3, 605)],
    ]

    result = await service.execute(
        template_id="delay_notice", recipients=recipients, provider=provider
    )
    assert result.payload["real_count"] == 2
    assert result.payload["simulated_count"] == 602
    assert result.payload["real_count"] + result.payload["simulated_count"] == 604
    assert result.provenance_kind == ProvenanceKind.real.value


async def test_honesty_note_states_what_was_actually_delivered(service):
    provider = ConsoleNotificationProvider()
    result = await service.execute(
        template_id="delay_notice", recipients=[_recipient(1)], provider=provider
    )
    note = result.payload["honesty_note"]
    assert "0 message(s) were actually delivered" in note
    assert "not sent anywhere" in note


async def test_every_notification_record_carries_a_delivery_mode(service):
    provider = ConsoleNotificationProvider()
    result = await service.execute(
        template_id="delay_notice",
        recipients=[_recipient(n) for n in range(1, 6)],
        provider=provider,
    )
    for record in result.payload["notifications"]:
        assert record["delivery_mode"] in {"real", "simulated"}


# ------------------------------------------------------------------------- refusals


async def test_missing_facts_block_dispatch_for_that_recipient(service):
    provider = ConsoleNotificationProvider()
    incomplete = _recipient(2)
    incomplete.facts["delay_minutes"] = None

    result = await service.execute(
        template_id="delay_notice",
        recipients=[_recipient(1), incomplete],
        provider=provider,
    )
    assert result.status is ActionStatus.needs_human
    assert result.payload["simulated_count"] == 1
    assert result.payload["not_rendered"] == [
        {"passenger_reference": "PAX-00002", "missing_facts": ["delay_minutes"]}
    ]
    assert len(provider.sent) == 1


async def test_unknown_template_is_needs_human(service):
    result = await service.execute(
        template_id="does_not_exist",
        recipients=[_recipient(1)],
        provider=ConsoleNotificationProvider(),
    )
    assert result.status is ActionStatus.needs_human
    assert "No approved template" in result.reason


@pytest.mark.parametrize("missing", ["template_id", "recipients", "provider"])
async def test_missing_input_is_needs_human(service, missing):
    kwargs = {
        "template_id": "delay_notice",
        "recipients": [_recipient(1)],
        "provider": ConsoleNotificationProvider(),
    }
    kwargs.pop(missing)
    result = await service.execute(**kwargs)
    assert result.status is ActionStatus.needs_human
    assert missing in result.reason
    assert result.payload["rule_version"] == RULE_VERSION


async def test_provider_failure_is_reported_not_swallowed(service):
    """A failed delivery reported as sent is the one outcome that must never happen."""

    class FailingProvider(ConsoleNotificationProvider):
        async def send_allowlisted(self, request):
            raise ProviderError(
                ProviderErrorKind.unavailable, "SMTP delivery failed", provider="smtp"
            )

    provider = FailingProvider(allowlist=["ops@skyforge.test"])
    result = await service.execute(
        template_id="delay_notice",
        recipients=[_recipient(1, "ops@skyforge.test"), _recipient(2)],
        provider=provider,
    )
    assert result.status is ActionStatus.needs_human
    assert result.payload["real_count"] == 0
    assert result.payload["provider_errors"][0]["kind"] == "unavailable"
    assert result.payload["simulated_count"] == 1


# --------------------------------------------------------------------- reproducibility


async def test_identical_input_yields_identical_output(service):
    def run():
        return service.execute(
            template_id="delay_notice",
            recipients=[_recipient(n) for n in range(1, 4)],
            provider=ConsoleNotificationProvider(now=None),
        )

    from datetime import UTC, datetime

    frozen = datetime(2026, 8, 20, 16, 0, tzinfo=UTC)
    first = await service.execute(
        template_id="delay_notice",
        recipients=[_recipient(n) for n in range(1, 4)],
        provider=ConsoleNotificationProvider(now=frozen),
    )
    second = await service.execute(
        template_id="delay_notice",
        recipients=[_recipient(n) for n in range(1, 4)],
        provider=ConsoleNotificationProvider(now=frozen),
    )
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    del run


async def test_evidence_names_the_template_version_and_every_passenger(service):
    result = await service.execute(
        template_id="delay_notice",
        recipients=[_recipient(1), _recipient(2)],
        provider=ConsoleNotificationProvider(),
    )
    assert "template:delay_notice:notify-v1" in result.evidence_refs
    assert "passenger:1" in result.evidence_refs
    assert "passenger:2" in result.evidence_refs
