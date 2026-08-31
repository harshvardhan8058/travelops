"""Mapping a provider flight-status reading into the SegmentFlight domain contract.

These tests pin the one translation between the vendor's vocabulary and the `SegmentFlight`
the connection service consumes. The behaviour that matters: a failed, cancelled or incomplete
status must never become an on-time, zero-delay segment. It must arrive as an explicit
`unavailable` result the orchestrator can route to `needs_human`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from app.models.enums import ProvenanceKind
from app.providers.base import ProviderError, ProviderErrorKind
from app.providers.flight_status.fixture import FixtureFlightStatusProvider
from app.providers.flight_status.live import LiveFlightStatusProvider
from app.services.connection import SegmentFlight
from app.services.flight_status_ingest import (
    ingest_flight_status,
    segment_from_status,
)

FROZEN_NOW = datetime(2026, 8, 21, 10, 30, tzinfo=UTC)


@pytest.fixture
def fixture_provider() -> FixtureFlightStatusProvider:
    return FixtureFlightStatusProvider(now=FROZEN_NOW)


async def test_a_delayed_flight_maps_to_a_segment_with_the_delay(fixture_provider):
    status = await fixture_provider.get_status(5002)
    result = segment_from_status(status, flight_id=5002)

    assert result.usable
    assert isinstance(result.segment, SegmentFlight)
    assert result.segment.delay_minutes == 95
    assert result.segment.origin_icao == "VOBL"
    assert result.segment.destination_icao == "VABB"
    assert result.provenance_kind == ProvenanceKind.fixture.value
    # Revised times follow from the delay the segment carries.
    assert result.segment.revised_departure > result.segment.scheduled_departure


async def test_an_on_time_flight_maps_to_a_zero_delay_segment(fixture_provider):
    status = await fixture_provider.get_status(5001)
    result = segment_from_status(status, flight_id=5001)
    assert result.usable
    assert result.segment.delay_minutes == 0


async def test_a_cancelled_flight_does_not_become_a_delayed_segment(fixture_provider):
    """A cancellation is a different event from a delay and is handled by recovery."""
    status = await fixture_provider.get_status(5004)
    result = segment_from_status(status, flight_id=5004)

    assert result.usable is False
    assert result.segment is None
    assert result.cancelled is True
    assert result.reason and "cancelled" in result.reason.lower()


async def test_evidence_refs_carry_the_flight_and_source(fixture_provider):
    status = await fixture_provider.get_status(5002)
    result = segment_from_status(status, flight_id=5002)
    assert "flight:5002" in result.evidence_refs
    assert any(ref.startswith("flight_status:") for ref in result.evidence_refs)


def test_a_status_missing_schedule_fields_is_not_usable():
    """No segment is invented from a status that lacks the times it needs."""
    incomplete = {
        "flight_id": 7,
        "flight_number": "AI999",
        "status": "active",
        "cancelled": False,
        "origin_icao": "VOBL",
        "destination_icao": None,
        "scheduled_departure": None,
        "scheduled_arrival": None,
        "delay_minutes": 0,
        "provenance": {
            "kind": ProvenanceKind.real.value,
            "provider": "aviationstack",
            "source_ref": "flight_status:7:x",
            "is_stale": False,
        },
    }
    result = segment_from_status(incomplete, flight_id=7)
    assert result.usable is False
    assert result.segment is None
    assert "destination_icao" in result.reason
    assert "scheduled_departure" in result.reason


def test_a_stale_status_flag_is_carried_through():
    stale = {
        "flight_id": 8,
        "flight_number": "AI111",
        "status": "active",
        "cancelled": False,
        "origin_icao": "VOBL",
        "destination_icao": "VIDP",
        "scheduled_departure": "2026-08-21T10:00:00+00:00",
        "scheduled_arrival": "2026-08-21T12:00:00+00:00",
        "delay_minutes": 10,
        "provenance": {
            "kind": ProvenanceKind.real.value,
            "provider": "aviationstack",
            "source_ref": "flight_status:8:x",
            "is_stale": True,
        },
    }
    result = segment_from_status(stale, flight_id=8)
    assert result.usable
    assert result.is_stale is True


async def test_ingest_turns_a_provider_error_into_unavailable(fixture_provider):
    """A missing flight raises ProviderError inside the provider; ingest must fail safe."""
    result = await ingest_flight_status(fixture_provider, flight_id=424242)
    assert result.usable is False
    assert result.provenance_kind == ProvenanceKind.unavailable.value
    assert result.reason and "unavailable" in result.reason.lower()
    assert result.evidence_refs == ["flight:424242"]


async def test_ingest_maps_a_live_timeout_to_unavailable():
    """The end-to-end fail-safe path: a live timeout must not surface as an on-time flight."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow", request=request)

    provider = LiveFlightStatusProvider(
        api_key="k",
        flight_index={5002: "6E512"},
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    result = await ingest_flight_status(provider, flight_id=5002)
    assert result.usable is False
    assert result.provenance_kind == ProvenanceKind.unavailable.value
    assert "timeout" in result.reason


async def test_ingest_succeeds_against_a_live_mock():
    import json

    from app.config import REPO_ROOT

    snapshot = json.loads(
        (REPO_ROOT / "fixtures" / "flight_status" / "aviationstack_snapshot.json").read_text()
    )

    def handler(request: httpx.Request) -> httpx.Response:
        wanted = request.url.params.get("flight_iata")
        rows = [r for r in snapshot["data"] if r.get("flight", {}).get("iata") == wanted]
        return httpx.Response(200, json={"data": rows})

    provider = LiveFlightStatusProvider(
        api_key="k",
        flight_index={5002: "6E512"},
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    result = await ingest_flight_status(provider, flight_id=5002)
    assert result.usable
    assert result.segment.delay_minutes == 95
    assert result.provenance_kind == ProvenanceKind.real.value


async def test_a_simulated_transition_maps_and_is_labelled_simulated(fixture_provider):
    await fixture_provider.apply_simulated_transition(5001, "delayed:60")
    status = await fixture_provider.get_status(5001)
    result = segment_from_status(status, flight_id=5001)
    assert result.usable
    assert result.segment.delay_minutes == 60
    assert result.provenance_kind == ProvenanceKind.simulated.value


def test_provider_error_is_the_only_failure_channel():
    """A sanity check that the mapper never raises for a bad dict — it returns unusable."""
    result = segment_from_status({"flight_id": 1, "provenance": {}}, flight_id=1)
    assert result.usable is False
    assert result.provenance_kind == ProvenanceKind.unavailable.value


def test_provider_error_type_is_importable():
    # Guards against a refactor that drops the typed-error dependency the fail-safe path needs.
    assert issubclass(ProviderError, Exception)
    assert ProviderErrorKind.unavailable.value == "unavailable"
