"""Flight-status provider contract — asserted against BOTH implementations.

The point of a provider Protocol is that the caller cannot tell which implementation it got, so
almost every behavioural test here runs against the live and the fixture provider and asserts
the same shape from each. The live provider is driven through an `httpx.MockTransport`, so this
file never touches the network.

The failure tests are the ones that matter most. A flight-status lookup that fails must surface
as a typed `ProviderError` — never as an on-time, zero-delay flight — because a fabricated
on-time status is indistinguishable downstream from a real one and would silently heal a broken
connection. AviationStack signals most failures with an HTTP 200 and an `{"error": ...}` body,
so those are pinned explicitly.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from app.config import REPO_ROOT
from app.models.enums import ProvenanceKind
from app.providers.base import (
    FlightStatusProvider,
    ProviderError,
    ProviderErrorKind,
    ProviderHealth,
)
from app.providers.flight_status import get_flight_status_provider
from app.providers.flight_status.fixture import (
    SNAPSHOT_FILE,
    FixtureFlightStatusProvider,
    load_snapshot,
)
from app.providers.flight_status.live import LiveFlightStatusProvider
from app.providers.flight_status.normalise import (
    delay_minutes_from_endpoint,
    normalise_status_row,
    utc_from_iso,
)

FROZEN_NOW = datetime(2026, 8, 21, 10, 30, tzinfo=UTC)

#: flight_id -> the IATA flight number the live provider must be told to look up.
FLIGHT_INDEX = {5001: "AI2811", 5002: "6E512", 5003: "AI440", 5004: "6E779"}


@pytest.fixture(scope="module")
def snapshot() -> dict:
    return load_snapshot(SNAPSHOT_FILE)


def _mock_live(snapshot: dict, *, now: datetime = FROZEN_NOW) -> LiveFlightStatusProvider:
    """A live provider whose transport replays the archived rows by flight_iata.

    Exercises the real HTTP path — URL building, status handling, the 200-with-error envelope,
    JSON decode — without a network, so the contract holds for both providers in CI.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        wanted = request.url.params.get("flight_iata")
        rows = [row for row in snapshot["data"] if row.get("flight", {}).get("iata") == wanted]
        return httpx.Response(200, json={"pagination": {"count": len(rows)}, "data": rows})

    class _FrozenLive(LiveFlightStatusProvider):
        def _now(self) -> datetime:  # deterministic staleness in tests
            return now

    return _FrozenLive(
        api_key="test-key",
        flight_index=FLIGHT_INDEX,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


@pytest.fixture
def providers(snapshot: dict) -> dict[str, FlightStatusProvider]:
    return {
        "live": _mock_live(snapshot),
        "fixture": FixtureFlightStatusProvider(now=FROZEN_NOW),
    }


BOTH = ["live", "fixture"]

#: Fields every normalised status row must expose, in both modes.
STATUS_KEYS = {
    "flight_id",
    "flight_number",
    "status",
    "status_is_known",
    "cancelled",
    "origin_icao",
    "destination_icao",
    "scheduled_departure",
    "scheduled_arrival",
    "revised_departure",
    "revised_arrival",
    "delay_minutes",
    "arrival_delay_minutes",
    "provenance",
}


# ------------------------------------------------------------------ protocol conformance


@pytest.mark.parametrize("mode", BOTH)
def test_both_implementations_satisfy_the_protocol(providers, mode):
    assert isinstance(providers[mode], FlightStatusProvider)


def test_selection_is_by_config_not_by_import_site():
    assert isinstance(get_flight_status_provider("fixture"), FixtureFlightStatusProvider)
    assert isinstance(get_flight_status_provider("live"), LiveFlightStatusProvider)


def test_selection_defaults_to_fixture_when_unspecified():
    """Fixture is the fail-safe default: live needs a key, fixture always works offline."""
    assert isinstance(get_flight_status_provider(), FixtureFlightStatusProvider)


def test_unknown_mode_raises_rather_than_defaulting():
    with pytest.raises(ValueError, match="unknown flight status mode"):
        get_flight_status_provider("satellite")


# -------------------------------------------------------------------- identical shapes


@pytest.mark.parametrize("mode", BOTH)
async def test_status_shape_is_identical_across_modes(providers, mode):
    status = await providers[mode].get_status(5002)
    assert set(status) == STATUS_KEYS
    assert status["flight_id"] == 5002
    assert status["origin_icao"] == "VOBL"
    assert status["destination_icao"] == "VABB"


@pytest.mark.parametrize("mode", BOTH)
async def test_both_modes_agree_on_the_vendor_reported_delay(providers, mode):
    """Flight 5002 carries an explicit 95-minute vendor delay in the snapshot."""
    status = await providers[mode].get_status(5002)
    assert status["delay_minutes"] == 95
    assert status["status"] == "active"


@pytest.mark.parametrize("mode", BOTH)
async def test_both_modes_derive_delay_from_times_when_vendor_omits_it(providers, mode):
    """Flight 5003 has no `delay` field; estimated is 40 minutes past scheduled."""
    status = await providers[mode].get_status(5003)
    assert status["delay_minutes"] == 40


@pytest.mark.parametrize("mode", BOTH)
async def test_health_reports_and_never_raises(providers, mode):
    health = await providers[mode].health()
    assert isinstance(health, ProviderHealth)
    assert health.healthy is True
    assert health.mode == mode


# ------------------------------------------------------------------------- provenance


@pytest.mark.parametrize("mode", BOTH)
async def test_every_status_carries_provenance(providers, mode):
    status = await providers[mode].get_status(5002)
    stamp = status["provenance"]
    assert stamp["provider"]
    assert stamp["retrieved_at"] is not None
    assert stamp["source_ref"]


async def test_live_status_is_labelled_real(providers):
    status = await providers["live"].get_status(5002)
    assert status["provenance"]["kind"] == ProvenanceKind.real.value


async def test_fixture_status_is_never_labelled_real(providers):
    """The archived bytes are shaped like a real capture, but a replay is not an observation."""
    status = await providers["fixture"].get_status(5002)
    assert status["provenance"]["kind"] == ProvenanceKind.fixture.value


async def test_stale_status_is_flagged(snapshot):
    """A status older than the five-minute limit must not read as current."""
    provider = FixtureFlightStatusProvider(now=FROZEN_NOW + timedelta(hours=2))
    status = await provider.get_status(5002)
    assert status["provenance"]["is_stale"] is True


async def test_fresh_status_is_not_flagged(providers):
    status = await providers["fixture"].get_status(5002)
    assert status["provenance"]["is_stale"] is False


# ------------------------------------------------------------------- determinism


async def test_fixture_output_is_reproducible():
    first = await FixtureFlightStatusProvider(now=FROZEN_NOW).get_status(5002)
    second = await FixtureFlightStatusProvider(now=FROZEN_NOW).get_status(5002)
    assert first == second


async def test_fixture_clock_defaults_to_the_snapshot_retrieval_time():
    status = await FixtureFlightStatusProvider().get_status(5002)
    snapshot = load_snapshot(SNAPSHOT_FILE)
    # Compared as instants, not strings: pydantic serialises +00:00 as Z, so a string
    # comparison would fail on formatting while the timestamps are identical.
    assert utc_from_iso(status["provenance"]["retrieved_at"]) == utc_from_iso(
        snapshot["source"]["retrieved_at"]
    )


# ------------------------------------------------------- simulated transitions (fixture only)


async def test_fixture_can_apply_a_cancellation(providers):
    provider = providers["fixture"]
    result = await provider.apply_simulated_transition(5002, "cancelled")
    assert result["cancelled"] is True
    assert result["status"] == "cancelled"
    assert result["delay_minutes"] == 0
    assert result["provenance"]["kind"] == ProvenanceKind.simulated.value


async def test_fixture_can_apply_a_delay(providers):
    provider = providers["fixture"]
    result = await provider.apply_simulated_transition(5001, "delayed:120")
    assert result["delay_minutes"] == 120
    assert result["provenance"]["kind"] == ProvenanceKind.simulated.value


async def test_simulated_transition_persists_for_subsequent_reads(providers):
    provider = providers["fixture"]
    await provider.apply_simulated_transition(5001, "delayed:75")
    status = await provider.get_status(5001)
    assert status["delay_minutes"] == 75
    assert status["provenance"]["kind"] == ProvenanceKind.simulated.value


async def test_malformed_delay_transition_is_invalid_response(providers):
    with pytest.raises(ProviderError) as exc:
        await providers["fixture"].apply_simulated_transition(5001, "delayed:soon")
    assert exc.value.kind is ProviderErrorKind.invalid_response


async def test_unsupported_transition_is_invalid_response(providers):
    with pytest.raises(ProviderError) as exc:
        await providers["fixture"].apply_simulated_transition(5001, "teleported")
    assert exc.value.kind is ProviderErrorKind.invalid_response


async def test_live_refuses_to_apply_a_simulated_transition(providers):
    """A live provider must never pretend to have moved a real flight."""
    with pytest.raises(ProviderError) as exc:
        await providers["live"].apply_simulated_transition(5002, "cancelled")
    assert exc.value.kind is ProviderErrorKind.forbidden


# ------------------------------------------------------------------------- typed errors


@pytest.mark.parametrize("mode", BOTH)
async def test_missing_flight_is_unavailable_not_silent_success(providers, mode):
    """A flight the source does not know must fail loudly, not return a blank on-time status."""
    with pytest.raises(ProviderError) as exc:
        await providers[mode].get_status(999999)
    assert exc.value.kind is ProviderErrorKind.unavailable


def _failing_live(handler) -> LiveFlightStatusProvider:
    return LiveFlightStatusProvider(
        api_key="k",
        flight_index=FLIGHT_INDEX,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (429, ProviderErrorKind.rate_limited),
        (403, ProviderErrorKind.forbidden),
        (401, ProviderErrorKind.forbidden),
        (500, ProviderErrorKind.unavailable),
        (503, ProviderErrorKind.unavailable),
        (400, ProviderErrorKind.invalid_response),
    ],
)
async def test_http_failures_map_to_typed_errors(status, expected):
    provider = _failing_live(lambda request: httpx.Response(status, text="nope"))
    with pytest.raises(ProviderError) as exc:
        await provider.get_status(5002)
    assert exc.value.kind is expected
    assert exc.value.provider == "aviationstack"


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("usage_limit_reached", ProviderErrorKind.rate_limited),
        ("rate_limit_reached", ProviderErrorKind.rate_limited),
        ("invalid_access_key", ProviderErrorKind.forbidden),
        ("missing_access_key", ProviderErrorKind.forbidden),
        ("inactive_user", ProviderErrorKind.forbidden),
        ("something_unmodelled", ProviderErrorKind.unavailable),
    ],
)
async def test_api_error_envelope_maps_to_typed_errors(code, expected):
    """AviationStack answers failures with HTTP 200 and an `error` object, not a 4xx/5xx."""
    provider = _failing_live(
        lambda request: httpx.Response(200, json={"error": {"code": code, "message": code}})
    )
    with pytest.raises(ProviderError) as exc:
        await provider.get_status(5002)
    assert exc.value.kind is expected


async def test_timeout_maps_to_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("too slow", request=request)

    with pytest.raises(ProviderError) as exc:
        await _failing_live(handler).get_status(5002)
    assert exc.value.kind is ProviderErrorKind.timeout


async def test_connection_failure_maps_to_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    with pytest.raises(ProviderError) as exc:
        await _failing_live(handler).get_status(5002)
    assert exc.value.kind is ProviderErrorKind.unavailable


async def test_no_content_is_unavailable():
    provider = _failing_live(lambda request: httpx.Response(204))
    with pytest.raises(ProviderError) as exc:
        await provider.get_status(5002)
    assert exc.value.kind is ProviderErrorKind.unavailable


async def test_malformed_body_maps_to_invalid_response():
    provider = _failing_live(lambda request: httpx.Response(200, text="<html>outage</html>"))
    with pytest.raises(ProviderError) as exc:
        await provider.get_status(5002)
    assert exc.value.kind is ProviderErrorKind.invalid_response


async def test_empty_data_list_is_unavailable():
    provider = _failing_live(
        lambda request: httpx.Response(200, json={"pagination": {"count": 0}, "data": []})
    )
    with pytest.raises(ProviderError) as exc:
        await provider.get_status(5002)
    assert exc.value.kind is ProviderErrorKind.unavailable


async def test_row_without_scheduled_departure_is_invalid_response():
    row = {"flight_status": "active", "departure": {"icao": "VOBL"}, "flight": {"iata": "6E512"}}
    provider = _failing_live(lambda request: httpx.Response(200, json={"data": [row]}))
    with pytest.raises(ProviderError) as exc:
        await provider.get_status(5002)
    assert exc.value.kind is ProviderErrorKind.invalid_response


async def test_live_without_a_flight_number_mapping_is_unavailable():
    """The domain id cannot be resolved to a vendor flight without an explicit mapping."""
    provider = LiveFlightStatusProvider(api_key="k", flight_index={})
    with pytest.raises(ProviderError) as exc:
        await provider.get_status(5002)
    assert exc.value.kind is ProviderErrorKind.unavailable


async def test_live_health_reports_down_without_a_key():
    """A live mode with no key is honestly down, not pretending to be healthy."""
    health = await LiveFlightStatusProvider(api_key="").health()
    assert health.healthy is False
    assert "key" in health.detail.lower()


async def test_live_health_reports_down_on_failure():
    provider = _failing_live(lambda request: httpx.Response(503))
    health = await provider.health()
    assert health.healthy is False
    assert "unavailable" in health.detail


# ------------------------------------------------------------------ normalisation units


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2024-03-30T09:55:00+00:00", datetime(2024, 3, 30, 9, 55, tzinfo=UTC)),
        ("2024-03-30T09:55:00Z", datetime(2024, 3, 30, 9, 55, tzinfo=UTC)),
        # Naive is assumed UTC rather than a guessed local zone.
        ("2024-03-30T09:55:00", datetime(2024, 3, 30, 9, 55, tzinfo=UTC)),
        (None, None),
        ("", None),
        ("not-a-time", None),
    ],
)
def test_iso_parsing_and_utc_normalisation(value, expected):
    assert utc_from_iso(value) == expected


def test_vendor_delay_field_wins_when_present():
    endpoint = {"delay": 42}
    assert delay_minutes_from_endpoint(endpoint, scheduled=None, best_known=None) == 42


def test_delay_is_derived_from_times_when_field_absent():
    scheduled = datetime(2026, 8, 21, 10, 0, tzinfo=UTC)
    revised = datetime(2026, 8, 21, 10, 40, tzinfo=UTC)
    assert delay_minutes_from_endpoint({}, scheduled=scheduled, best_known=revised) == 40


def test_an_early_departure_is_zero_delay_never_negative():
    """A negative delay would make a revised arrival earlier than scheduled and silently heal
    a broken connection."""
    scheduled = datetime(2026, 8, 21, 10, 0, tzinfo=UTC)
    early = datetime(2026, 8, 21, 9, 45, tzinfo=UTC)
    assert delay_minutes_from_endpoint({}, scheduled=scheduled, best_known=early) == 0
    assert delay_minutes_from_endpoint({"delay": -5}, scheduled=None, best_known=None) == 0


def test_unknown_status_is_preserved_and_flagged_not_known():
    row = {
        "flight_status": "boarding",
        "departure": {"icao": "VOBL", "scheduled": "2026-08-21T10:00:00+00:00"},
        "arrival": {"icao": "VIDP", "scheduled": "2026-08-21T12:00:00+00:00"},
        "flight": {"iata": "AI2811"},
    }
    normalised = normalise_status_row(row, flight_id=1, provider="test")
    assert normalised["status"] == "boarding"
    assert normalised["status_is_known"] is False


def test_icao_is_used_and_iata_is_not_substituted():
    """A silent IATA-for-ICAO swap would key nothing and drop the segment from the walk."""
    row = {
        "flight_status": "active",
        "departure": {"iata": "BLR", "scheduled": "2026-08-21T10:00:00+00:00"},
        "arrival": {"icao": "VIDP", "scheduled": "2026-08-21T12:00:00+00:00"},
        "flight": {"iata": "AI2811"},
    }
    normalised = normalise_status_row(row, flight_id=1, provider="test")
    assert normalised["origin_icao"] is None
    assert normalised["destination_icao"] == "VIDP"


# ---------------------------------------------------------------------------- the archive


def test_snapshot_records_its_source_and_licence(snapshot):
    source = snapshot["source"]
    assert source["provider"] == "aviationstack"
    assert source["licence"]
    assert source["endpoint"].startswith("https://api.aviationstack.com/")
    assert "SHAPED-LIKE-CAPTURE" in source["note"]


def test_snapshot_is_valid_json_on_disk():
    json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))


def test_snapshot_covers_the_documented_scenarios(snapshot):
    scenarios = {row["_fixture"]["scenario"] for row in snapshot["data"]}
    assert scenarios == {"on_time", "delayed", "delay_derived_from_times", "cancelled"}


def test_fixture_dir_matches_repo_layout():
    assert SNAPSHOT_FILE == REPO_ROOT / "fixtures" / "flight_status" / "aviationstack_snapshot.json"
    assert Path(SNAPSHOT_FILE).is_file()
