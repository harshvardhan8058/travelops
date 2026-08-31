"""Observed flight status, from configuration through to a changed connection verdict.

Stream C shipped a complete, tested flight-status provider in #99 and nothing called it: the
services are pure and hold no session, so nothing could turn a domain `flight_id` into the IATA
number AviationStack knows, and nothing put the answer in front of the connection walk. These
tests cover the seam that closes that gap, and they are written to fail for the three ways this
kind of integration usually goes wrong quietly:

1. **A live badge over replayed data.** `FLIGHT_STATUS_MODE=live` with no key must refuse to
   start, and a permitted fallback must be recorded where an operator can see it.
2. **A lookup that always misses.** The domain stores `"6E 2134"`; the vendor knows `6E2134`.
   Passing the stored string through is a lookup that never matches, and "no current status for
   6E 2134" reads like a flight the vendor does not track rather than the mapping bug it is.
3. **A failure that reads as good news.** A timeout, a rate limit or a cancellation must never
   leave the analysis reporting an on-time flight. The derived figure stands, and the reason the
   observed one could not be used is recorded per flight.

The headline test drives the real `LiveFlightStatusProvider` over a mocked transport, through
the real normaliser, the real ingest mapping, the real adapter and the real `ConnectionService`,
against a real database. Only the network is a stand-in — so it demonstrates that live data
reaches and changes the decision input rather than asserting that it would.

Owner: Stream A (the seam) / Stream C (the provider, the normaliser and the ingest mapping).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.config import (
    ConfigurationError,
    FlightStatusMode,
    Settings,
    resolve_modes,
)
from app.models.enums import ProvenanceKind
from app.models.reference import (
    Airport,
    Booking,
    BookingSegment,
    Flight,
    Passenger,
)
from app.orchestrator.flight_status_adapter import (
    PAYLOAD_KEY,
    apply_live_flight_status,
    build_flight_index,
    external_flight_number,
    merge_into_result,
)
from app.orchestrator.service_registry import run_connection
from app.providers.base import ProviderError, ProviderErrorKind
from app.providers.flight_status import (
    FixtureFlightStatusProvider,
    LiveFlightStatusProvider,
    get_flight_status_provider,
)

FIXED_NOW = datetime(2026, 8, 20, 10, 0, tzinfo=UTC)

#: Snapshot flight ids, from `fixtures/flight_status/aviationstack_snapshot.json`.
FIXTURE_ON_TIME = 5001
FIXTURE_VENDOR_DELAY = 5002  # vendor reports `delay: 95`
FIXTURE_DERIVED_DELAY = 5003  # no `delay`; estimated is 40 minutes past scheduled
FIXTURE_CANCELLED = 5004


def _settings(**overrides) -> Settings:
    base = {
        "app_env": "test",
        "flight_status_mode": FlightStatusMode.live,
        "aviationstack_api_key": "test-key",
    }
    return Settings(_env_file=None, **{**base, **overrides})


# --------------------------------------------------------------------------- vendor payloads


def _vendor_row(
    *,
    flight_iata: str,
    origin_icao: str,
    destination_icao: str,
    scheduled_departure: datetime,
    scheduled_arrival: datetime,
    delay: int | None = None,
    estimated_departure: datetime | None = None,
    status: str = "active",
) -> dict:
    """One AviationStack `data` row, shaped exactly like the committed snapshot's."""
    return {
        "flight_date": scheduled_departure.date().isoformat(),
        "flight_status": status,
        "departure": {
            "iata": origin_icao[1:],
            "icao": origin_icao,
            "scheduled": scheduled_departure.isoformat(),
            "estimated": estimated_departure.isoformat() if estimated_departure else None,
            "actual": None,
            "delay": delay,
        },
        "arrival": {
            "iata": destination_icao[1:],
            "icao": destination_icao,
            "scheduled": scheduled_arrival.isoformat(),
            "estimated": None,
            "actual": None,
            "delay": delay,
        },
        "airline": {"iata": flight_iata[:2], "icao": "IGO"},
        "flight": {"number": flight_iata[2:], "iata": flight_iata, "icao": f"IGO{flight_iata[2:]}"},
    }


class _Vendor:
    """A mocked AviationStack, recording every flight number it was asked for."""

    def __init__(self, rows_by_number: dict[str, dict], *, status_code: int = 200) -> None:
        self.rows_by_number = rows_by_number
        self.status_code = status_code
        self.asked: list[str] = []
        self.keys_seen: list[str] = []

    def provider(self, *, flight_index: dict[int, str], api_key: str = "test-key"):
        def handle(request: httpx.Request) -> httpx.Response:
            number = request.url.params.get("flight_iata", "")
            self.asked.append(number)
            self.keys_seen.append(request.url.params.get("access_key", ""))
            if self.status_code != 200:
                return httpx.Response(
                    self.status_code, json={"error": {"message": "nope", "code": "boom"}}
                )
            row = self.rows_by_number.get(number)
            return httpx.Response(200, json={"data": [row] if row else []})

        return LiveFlightStatusProvider(
            api_key=api_key,
            flight_index=flight_index,
            client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        )


class _Failing:
    """A provider that fails the way the real one does: a typed `ProviderError`."""

    name = "aviationstack"
    mode = "live"

    def __init__(self, kind: ProviderErrorKind, message: str) -> None:
        self.kind = kind
        self.message = message
        self.calls: list[int] = []

    async def get_status(self, flight_id: int) -> dict:
        self.calls.append(flight_id)
        raise ProviderError(self.kind, self.message, provider=self.name)


# ------------------------------------------------------------------------------- the dataset


@pytest.fixture
async def connecting_itinerary(session):
    """One passenger with a real connection: VOBL->VIDP, then VIDP->VABB 60 minutes later.

    The default minimum connection is 45 minutes, so the connection holds while the inbound is
    on time and breaks once the inbound loses more than 15 minutes. That margin is what makes an
    observed delay visible as a changed verdict rather than as an unchanged count.
    """
    session.add_all(
        [
            Airport(
                icao_code=icao,
                iata_code=icao[1:],
                name=f"{icao} airport",
                city=icao,
                country="IN",
                latitude=13.0,
                longitude=77.0,
                source_ref="fixture:test",
            )
            for icao in ("VOBL", "VIDP", "VABB")
        ]
    )
    inbound = Flight(
        flight_number="6E 2134",
        airline_code="6E",
        origin_icao="VOBL",
        destination_icao="VIDP",
        scheduled_departure=FIXED_NOW,
        scheduled_arrival=FIXED_NOW + timedelta(hours=2),
        block_time_minutes=120,
        status="scheduled",
        is_domestic=True,
        provenance_kind=ProvenanceKind.fixture,
        source_ref="fixture:test:inbound",
    )
    onward = Flight(
        flight_number="6E 512",
        airline_code="6E",
        origin_icao="VIDP",
        destination_icao="VABB",
        scheduled_departure=FIXED_NOW + timedelta(hours=3),
        scheduled_arrival=FIXED_NOW + timedelta(hours=5),
        block_time_minutes=120,
        status="scheduled",
        is_domestic=True,
        provenance_kind=ProvenanceKind.fixture,
        source_ref="fixture:test:onward",
    )
    session.add_all([inbound, onward])
    await session.flush()

    passenger = Passenger(
        reference="PAX-00001",
        full_name="Test Passenger",
        email="pax-00001@example.com",
        tier="standard",
        provenance_kind=ProvenanceKind.synthetic,
    )
    session.add(passenger)
    await session.flush()
    booking = Booking(pnr="TEST01", passenger_id=passenger.id, cabin="economy")
    session.add(booking)
    await session.flush()
    session.add_all(
        [
            BookingSegment(booking_id=booking.id, flight_id=inbound.id, segment_order=1),
            BookingSegment(booking_id=booking.id, flight_id=onward.id, segment_order=2),
        ]
    )
    await session.commit()
    return inbound, onward


# =========================================================== 1. configuration is fail-closed


class TestLiveModeCannotBeClaimedWithoutCredentials:
    def test_fixture_is_the_default_so_a_fresh_clone_runs_offline(self):
        settings = Settings(_env_file=None)
        assert settings.flight_status_mode is FlightStatusMode.fixture
        assert settings.aviationstack_api_key == ""
        assert settings.allow_flight_status_degradation is False

    def test_live_without_a_key_refuses_to_start(self):
        """Fail closed at startup, not per incident.

        Discovering this inside a run means every connection check reports an unavailable source
        and the operator has to infer a missing environment variable from a wall of provider
        errors. The message has to name the variable to set.
        """
        with pytest.raises(ConfigurationError) as caught:
            resolve_modes(_settings(aviationstack_api_key=""))

        message = str(caught.value)
        assert "AVIATIONSTACK_API_KEY" in message
        assert "FLIGHT_STATUS_MODE=fixture" in message

    def test_live_with_a_key_resolves_to_live_with_nothing_degraded(self):
        modes = resolve_modes(_settings())
        assert modes.flight_status is FlightStatusMode.live
        assert modes.degradations == []

    def test_a_permitted_fallback_is_recorded_rather_than_silent(self):
        """Never silently fall back from live to fixture.

        The opt-in exists, but taking it has to leave a mark: the published mode becomes the
        effective one and the reason appears in `degradations`, which `GET /system/mode` and the
        readiness probe both republish.
        """
        modes = resolve_modes(
            _settings(aviationstack_api_key="", allow_flight_status_degradation=True)
        )

        assert modes.flight_status is FlightStatusMode.fixture
        assert any("AVIATIONSTACK_API_KEY" in entry for entry in modes.degradations)

    def test_the_published_mode_is_the_effective_one_and_never_the_key(self):
        published = resolve_modes(_settings()).to_dict()

        assert published["flight_status_mode"] == "live"
        # A degraded request must not still advertise live.
        degraded = resolve_modes(
            _settings(aviationstack_api_key="", allow_flight_status_degradation=True)
        ).to_dict()
        assert degraded["flight_status_mode"] == "fixture"
        assert "test-key" not in json.dumps(published)

    def test_weather_stays_independently_selectable_and_needs_no_key(self):
        """The existing AWC provider is untouched and still flips cleanly.

        Weather is deliberately not given the same startup gate: AWC is a public-domain
        US-government API with no credential, so `WEATHER_MODE=live` must keep working with
        nothing else configured. Coupling the two would have made live weather need a
        flight-status key.
        """
        from app.config import WeatherMode
        from app.providers.weather import (
            FixtureWeatherProvider,
            LiveWeatherProvider,
            get_weather_provider,
        )

        # Selectable purely through configuration, with a flight-status key present or absent.
        assert resolve_modes(_settings(weather_mode="live")).weather is WeatherMode.live
        assert (
            resolve_modes(
                _settings(
                    weather_mode="live",
                    flight_status_mode=FlightStatusMode.fixture,
                    aviationstack_api_key="",
                )
            ).weather
            is WeatherMode.live
        )
        assert resolve_modes(_settings(weather_mode="fixture")).weather is WeatherMode.fixture
        assert isinstance(get_weather_provider(WeatherMode.live), LiveWeatherProvider)
        assert isinstance(get_weather_provider(WeatherMode.fixture), FixtureWeatherProvider)
        # And the flight-status selector still answers for its own modes, unchanged.
        assert isinstance(get_flight_status_provider("fixture"), FixtureFlightStatusProvider)


# ================================================== 2. the domain id reaches the right flight


class TestTheFlightNumberMapping:
    @pytest.mark.parametrize(
        ("stored", "vendor"),
        [("6E 2134", "6E2134"), ("UK 705", "UK705"), ("AI 440", "AI440"), ("6E512", "6E512")],
    )
    def test_the_stored_space_is_stripped_for_the_vendor(self, stored, vendor):
        assert external_flight_number(stored) == vendor

    def test_nothing_else_about_the_number_is_invented(self):
        """Whitespace only, so a number the vendor genuinely does not know fails as itself."""
        assert external_flight_number("ZZ 9999") == "ZZ9999"

    async def test_the_index_is_read_from_the_database_and_scoped_to_the_request(
        self, session, connecting_itinerary
    ):
        inbound, onward = connecting_itinerary

        both = await build_flight_index(session, {inbound.id, onward.id})
        assert both == {inbound.id: "6E2134", onward.id: "6E512"}

        # A connection check on one flight must not read the whole flight table.
        one = await build_flight_index(session, {inbound.id})
        assert one == {inbound.id: "6E2134"}
        assert await build_flight_index(session, set()) == {}

    async def test_the_index_is_what_the_vendor_is_actually_asked_for(
        self, session, connecting_itinerary
    ):
        """End of the mapping chain: the request carries `6E2134`, not `6E 2134`.

        Asserted on the outbound HTTP request rather than on the index, because the index being
        right is worth nothing if the provider is handed something else.
        """
        inbound, onward = connecting_itinerary
        vendor = _Vendor({})
        provider = vendor.provider(
            flight_index=await build_flight_index(session, {inbound.id, onward.id})
        )

        _, flights = await _load(session, {inbound.id})
        await apply_live_flight_status(session, flights, settings=_settings(), provider=provider)

        assert sorted(vendor.asked) == ["6E2134", "6E512"]
        assert " " not in "".join(vendor.asked)


async def _load(session, flight_ids):
    from app.db.scenario_queries import load_connection_inputs

    return await load_connection_inputs(session, flight_ids)


# ===================================== 3. live data reaches, and changes, the decision input


class TestAnObservedDelayChangesTheDecision:
    async def test_the_connection_holds_before_the_live_reading(
        self, session, connecting_itinerary
    ):
        """The baseline. Without it, the test below proves nothing about causation."""
        inbound, _onward = connecting_itinerary

        result = await run_connection(session=session, target_refs=[f"flight:{inbound.id}"])

        assert result.payload["at_risk_count"] == 0
        # Fixture is the default, so no provider was consulted at all.
        assert PAYLOAD_KEY not in result.payload

    async def test_a_live_delay_breaks_the_same_connection(
        self, session, connecting_itinerary, monkeypatch
    ):
        """The claim this whole integration exists to support.

        Same database, same itinerary, same service. The only thing that changed is what
        AviationStack reported, and the verdict moves from 0 broken connections to 1. The real
        provider, normaliser, ingest mapping, adapter and connection service all take part; only
        the socket is a stand-in.
        """
        inbound, _onward = connecting_itinerary
        vendor = _Vendor(
            {
                "6E2134": _vendor_row(
                    flight_iata="6E2134",
                    origin_icao="VOBL",
                    destination_icao="VIDP",
                    scheduled_departure=FIXED_NOW,
                    scheduled_arrival=FIXED_NOW + timedelta(hours=2),
                    delay=30,
                ),
                "6E512": _vendor_row(
                    flight_iata="6E512",
                    origin_icao="VIDP",
                    destination_icao="VABB",
                    scheduled_departure=FIXED_NOW + timedelta(hours=3),
                    scheduled_arrival=FIXED_NOW + timedelta(hours=5),
                    delay=0,
                ),
            }
        )
        _install_live(monkeypatch, vendor)

        result = await run_connection(session=session, target_refs=[f"flight:{inbound.id}"])

        assert result.payload["at_risk_count"] == 1, (
            "a 30-minute observed delay must consume the 15 minutes of slack this "
            "connection had, or live data is not reaching the walk"
        )
        overlay = result.payload[PAYLOAD_KEY]
        assert overlay["mode"] == "live"
        assert overlay["consulted"] is True
        assert overlay["changed_decision_input"] is True
        assert overlay["applied"][str(inbound.id)] == 30
        assert overlay["replaced"][str(inbound.id)] == 0
        assert overlay["provenance_kinds"][str(inbound.id)] == ProvenanceKind.real.value

    async def test_the_delay_figure_itself_is_what_moved_the_verdict(
        self, session, connecting_itinerary, monkeypatch
    ):
        """A smaller delay inside the slack must NOT break it.

        Without this, a test that only ever asserts "live delay breaks the connection" would
        also pass if the overlay were breaking connections for some reason other than the
        number it read.
        """
        inbound, _onward = connecting_itinerary
        vendor = _Vendor(
            {
                "6E2134": _vendor_row(
                    flight_iata="6E2134",
                    origin_icao="VOBL",
                    destination_icao="VIDP",
                    scheduled_departure=FIXED_NOW,
                    scheduled_arrival=FIXED_NOW + timedelta(hours=2),
                    delay=10,
                ),
            }
        )
        _install_live(monkeypatch, vendor)

        result = await run_connection(session=session, target_refs=[f"flight:{inbound.id}"])

        assert result.payload["at_risk_count"] == 0
        assert result.payload[PAYLOAD_KEY]["applied"][str(inbound.id)] == 10

    async def test_the_external_reading_is_cited_in_the_evidence(
        self, session, connecting_itinerary, monkeypatch
    ):
        """An observed figure that changed a decision has to be traceable to its source."""
        inbound, _onward = connecting_itinerary
        vendor = _Vendor(
            {
                "6E2134": _vendor_row(
                    flight_iata="6E2134",
                    origin_icao="VOBL",
                    destination_icao="VIDP",
                    scheduled_departure=FIXED_NOW,
                    scheduled_arrival=FIXED_NOW + timedelta(hours=2),
                    delay=30,
                )
            }
        )
        _install_live(monkeypatch, vendor)

        result = await run_connection(session=session, target_refs=[f"flight:{inbound.id}"])

        cited = [ref for ref in result.evidence_refs if ref.startswith("flight_status:")]
        assert cited, f"no flight_status evidence ref in {result.evidence_refs}"

    async def test_the_schedule_stays_the_domains_and_only_the_delay_is_observed(
        self, session, connecting_itinerary, monkeypatch
    ):
        """A vendor may report the delay; it may not redefine the timetable tickets were sold on.

        The vendor here claims a completely different scheduled departure. If that were adopted,
        the walk would be comparing the vendor's timetable with itself while still reporting a
        passenger count against bookings made on the real one.
        """
        inbound, _onward = connecting_itinerary
        vendor = _Vendor(
            {
                "6E2134": _vendor_row(
                    flight_iata="6E2134",
                    origin_icao="VOBL",
                    destination_icao="VIDP",
                    scheduled_departure=FIXED_NOW + timedelta(hours=9),
                    scheduled_arrival=FIXED_NOW + timedelta(hours=11),
                    delay=30,
                )
            }
        )
        provider = vendor.provider(flight_index={inbound.id: "6E2134"})
        _, flights = await _load(session, {inbound.id})

        updated, overlay = await apply_live_flight_status(
            session, flights, settings=_settings(), provider=provider
        )

        segment = updated[inbound.id]
        assert segment.scheduled_departure == FIXED_NOW
        assert segment.scheduled_arrival == FIXED_NOW + timedelta(hours=2)
        assert segment.flight_number == "6E 2134"
        assert segment.delay_minutes == 30
        assert overlay.applied[inbound.id] == 30


def _install_live(monkeypatch, vendor: _Vendor) -> None:
    """Point the adapter at live mode and hand it the mocked vendor transport.

    `Settings` and the provider class are replaced; the adapter, the index build, the
    normaliser, the ingest mapping and the connection service are all the real thing.
    """
    monkeypatch.setattr("app.orchestrator.flight_status_adapter.get_settings", lambda: _settings())

    def _factory(*, api_key: str = "", flight_index=None, **_kwargs):
        return vendor.provider(flight_index=dict(flight_index or {}), api_key=api_key)

    monkeypatch.setattr("app.providers.flight_status.LiveFlightStatusProvider", _factory)


# ============================================================ 4. failure is never good news


class TestAFailedLookupNeverReadsAsOnTime:
    @pytest.mark.parametrize(
        ("kind", "message"),
        [
            (ProviderErrorKind.timeout, "AviationStack did not respond within 8.0s"),
            (ProviderErrorKind.rate_limited, "monthly quota reached"),
            (ProviderErrorKind.forbidden, "invalid access key"),
            (ProviderErrorKind.unavailable, "no current status for 6E2134"),
            (ProviderErrorKind.invalid_response, "body is not JSON"),
        ],
    )
    async def test_the_derived_delay_stands_and_the_reason_is_recorded(
        self, session, connecting_itinerary, kind, message
    ):
        inbound, _onward = connecting_itinerary
        _, flights = await _load(session, {inbound.id})
        before = flights[inbound.id].delay_minutes
        failing = _Failing(kind, message)

        updated, overlay = await apply_live_flight_status(
            session, flights, settings=_settings(), provider=failing
        )

        assert updated[inbound.id].delay_minutes == before
        assert overlay.applied == {}
        assert kind.value in overlay.unusable[inbound.id]
        assert message in overlay.unusable[inbound.id]
        assert overlay.provenance_kinds[inbound.id] == ProvenanceKind.unavailable.value
        # Consulted, and it failed. Distinct from "no provider was asked".
        assert overlay.consulted is True
        assert overlay.changed_any_input is False

    async def test_a_provider_failure_does_not_fail_the_action(
        self, session, connecting_itinerary, monkeypatch
    ):
        """The connection check still runs on the figures the domain has. Nothing raises."""
        inbound, _onward = connecting_itinerary
        vendor = _Vendor({}, status_code=500)
        _install_live(monkeypatch, vendor)

        result = await run_connection(session=session, target_refs=[f"flight:{inbound.id}"])

        assert result.payload["at_risk_count"] == 0
        overlay = result.payload[PAYLOAD_KEY]
        assert overlay["applied"] == {}
        assert overlay["unusable"], "a failed sweep must say so"
        assert overlay["changed_decision_input"] is False

    async def test_an_http_error_is_recorded_per_flight_not_once_for_the_run(
        self, session, connecting_itinerary, monkeypatch
    ):
        inbound, onward = connecting_itinerary
        vendor = _Vendor({}, status_code=429)
        _install_live(monkeypatch, vendor)

        result = await run_connection(session=session, target_refs=[f"flight:{inbound.id}"])

        unusable = result.payload[PAYLOAD_KEY]["unusable"]
        assert set(unusable) == {str(inbound.id), str(onward.id)}
        assert all("rate_limited" in reason for reason in unusable.values())

    async def test_a_flight_the_vendor_does_not_know_is_reported_not_guessed(
        self, session, connecting_itinerary
    ):
        """No index entry means no lookup. The provider refuses rather than inventing a number."""
        inbound, _onward = connecting_itinerary
        vendor = _Vendor({})
        provider = vendor.provider(flight_index={})
        _, flights = await _load(session, {inbound.id})

        _updated, overlay = await apply_live_flight_status(
            session, flights, settings=_settings(), provider=provider
        )

        assert vendor.asked == [], "a flight with no mapping must not reach the vendor"
        assert "no external flight number is mapped" in overlay.unusable[inbound.id]

    async def test_a_cancellation_is_not_overlaid_as_a_delay(self, session, connecting_itinerary):
        """A cancelled flight modelled as a huge delay could be "recovered" by a late onward.

        Stream C's ingest refuses to build a segment for a cancellation; this pins that the
        refusal survives the adapter instead of being turned into a number.
        """
        inbound, _onward = connecting_itinerary
        vendor = _Vendor(
            {
                "6E2134": _vendor_row(
                    flight_iata="6E2134",
                    origin_icao="VOBL",
                    destination_icao="VIDP",
                    scheduled_departure=FIXED_NOW,
                    scheduled_arrival=FIXED_NOW + timedelta(hours=2),
                    status="cancelled",
                )
            }
        )
        provider = vendor.provider(flight_index={inbound.id: "6E2134"})
        _, flights = await _load(session, {inbound.id})
        before = flights[inbound.id].delay_minutes

        updated, overlay = await apply_live_flight_status(
            session, flights, settings=_settings(), provider=provider
        )

        assert updated[inbound.id].delay_minutes == before
        assert inbound.id not in overlay.applied
        assert "cancelled" in overlay.unusable[inbound.id]


# ================================================================== 5. the delay mapping itself


class TestDelayMapping:
    async def test_the_vendors_own_delay_field_is_used(self, session, connecting_itinerary):
        inbound, _onward = connecting_itinerary
        _, flights = await _load(session, {inbound.id})
        remapped = {FIXTURE_VENDOR_DELAY: flights[inbound.id]}

        _updated, overlay = await apply_live_flight_status(
            session,
            remapped,
            settings=_settings(),
            provider=FixtureFlightStatusProvider(now=FIXED_NOW),
        )

        assert overlay.applied[FIXTURE_VENDOR_DELAY] == 95

    async def test_a_missing_delay_field_is_derived_from_the_timestamps(
        self, session, connecting_itinerary
    ):
        inbound, _onward = connecting_itinerary
        _, flights = await _load(session, {inbound.id})
        remapped = {FIXTURE_DERIVED_DELAY: flights[inbound.id]}

        _updated, overlay = await apply_live_flight_status(
            session,
            remapped,
            settings=_settings(),
            provider=FixtureFlightStatusProvider(now=FIXED_NOW),
        )

        assert overlay.applied[FIXTURE_DERIVED_DELAY] == 40

    async def test_an_on_time_flight_applies_zero_and_changes_nothing(
        self, session, connecting_itinerary
    ):
        inbound, _onward = connecting_itinerary
        _, flights = await _load(session, {inbound.id})
        remapped = {FIXTURE_ON_TIME: flights[inbound.id]}

        updated, overlay = await apply_live_flight_status(
            session,
            remapped,
            settings=_settings(),
            provider=FixtureFlightStatusProvider(now=FIXED_NOW),
        )

        assert overlay.applied[FIXTURE_ON_TIME] == 0
        assert updated[FIXTURE_ON_TIME].delay_minutes == 0
        assert overlay.changed_any_input is False

    async def test_an_early_departure_is_zero_delay_not_a_negative_one(
        self, session, connecting_itinerary
    ):
        """A negative delay would move a revised arrival earlier and heal a broken connection.

        Stream C clamps it in the normaliser; this pins that the clamp is still in force where
        the figure actually gets used.
        """
        inbound, _onward = connecting_itinerary
        vendor = _Vendor(
            {
                "6E2134": _vendor_row(
                    flight_iata="6E2134",
                    origin_icao="VOBL",
                    destination_icao="VIDP",
                    scheduled_departure=FIXED_NOW,
                    scheduled_arrival=FIXED_NOW + timedelta(hours=2),
                    delay=-25,
                )
            }
        )
        provider = vendor.provider(flight_index={inbound.id: "6E2134"})
        _, flights = await _load(session, {inbound.id})

        updated, overlay = await apply_live_flight_status(
            session, flights, settings=_settings(), provider=provider
        )

        assert overlay.applied[inbound.id] == 0
        assert updated[inbound.id].revised_arrival >= updated[inbound.id].scheduled_arrival


# ======================================================= 6. fixture and live are the same shape


class TestFixtureAndLiveParity:
    async def test_the_same_vendor_row_produces_the_same_domain_segment(
        self, session, connecting_itinerary
    ):
        """One normalisation path, so a replayed status and an observed one are shaped alike.

        The snapshot row for 5002 is fed to the live provider over a mocked transport and read
        from the fixture provider, and the resulting `SegmentFlight` must be identical field for
        field. Only the provenance differs — which is the one thing that must NOT be identical.
        """
        snapshot = json.loads(
            (_repo_root() / "fixtures" / "flight_status" / "aviationstack_snapshot.json").read_text(
                encoding="utf-8"
            )
        )
        row = next(r for r in snapshot["data"] if r["_fixture"]["flight_id"] == 5002)

        live = LiveFlightStatusProvider(
            api_key="test-key",
            flight_index={FIXTURE_VENDOR_DELAY: "6E512"},
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(200, json={"data": [row]})
                )
            ),
        )
        fixture = FixtureFlightStatusProvider(now=FIXED_NOW)

        from app.services.flight_status_ingest import ingest_flight_status

        live_result = await ingest_flight_status(live, flight_id=FIXTURE_VENDOR_DELAY)
        fixture_result = await ingest_flight_status(fixture, flight_id=FIXTURE_VENDOR_DELAY)

        assert live_result.usable and fixture_result.usable
        assert live_result.segment == fixture_result.segment
        assert live_result.delay_minutes == fixture_result.delay_minutes == 95
        assert live_result.provenance_kind == ProvenanceKind.real.value
        assert fixture_result.provenance_kind == ProvenanceKind.fixture.value

    async def test_both_modes_overlay_through_the_identical_adapter_path(
        self, session, connecting_itinerary
    ):
        """The adapter treats the two providers the same, so a demo and a live run agree."""
        inbound, _onward = connecting_itinerary
        _, flights = await _load(session, {inbound.id})
        row = _vendor_row(
            flight_iata="6E512",
            origin_icao="VOBL",
            destination_icao="VABB",
            scheduled_departure=datetime(2026, 8, 21, 10, 29, tzinfo=UTC),
            scheduled_arrival=datetime(2026, 8, 21, 12, 10, tzinfo=UTC),
            delay=95,
        )
        live = LiveFlightStatusProvider(
            api_key="k",
            flight_index={FIXTURE_VENDOR_DELAY: "6E512"},
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(200, json={"data": [row]})
                )
            ),
        )
        remapped = {FIXTURE_VENDOR_DELAY: flights[inbound.id]}

        _live_map, live_overlay = await apply_live_flight_status(
            session, remapped, settings=_settings(), provider=live
        )
        _fixture_map, fixture_overlay = await apply_live_flight_status(
            session,
            remapped,
            settings=_settings(flight_status_mode=FlightStatusMode.fixture),
            provider=FixtureFlightStatusProvider(now=FIXED_NOW),
        )

        assert live_overlay.applied == fixture_overlay.applied == {FIXTURE_VENDOR_DELAY: 95}
        assert live_overlay.mode == "live"
        assert fixture_overlay.mode == "fixture"
        assert (
            live_overlay.provenance_kinds[FIXTURE_VENDOR_DELAY]
            != fixture_overlay.provenance_kinds[FIXTURE_VENDOR_DELAY]
        )


def _repo_root():
    from app.config import REPO_ROOT

    return REPO_ROOT


# ============================================== 7. Phase 1-4 behaviour is provably untouched


class TestTheDefaultPathIsUnchanged:
    async def test_fixture_mode_consults_no_provider_and_returns_the_map_intact(
        self, session, connecting_itinerary
    ):
        """The whole Phase 1-4 path runs through here, so a no-op has to be a real no-op.

        The committed snapshot describes the vendor's own flight ids, not this dataset's, so
        sweeping it in fixture mode would report "no current status" for every flight and fill
        the audit trail with failures that mean nothing. The seeded `estimated_departure` the
        domain derives from is already the fixture flight state.
        """
        inbound, _onward = connecting_itinerary
        _, flights = await _load(session, {inbound.id})

        updated, overlay = await apply_live_flight_status(
            session, flights, settings=_settings(flight_status_mode=FlightStatusMode.fixture)
        )

        assert updated == flights
        assert overlay.consulted is False
        assert overlay.applied == {}
        assert overlay.unusable == {}
        assert overlay.mode == "fixture"

    async def test_an_unconsulted_overlay_adds_nothing_to_the_result(
        self, session, connecting_itinerary
    ):
        """No key in the payload at all, so an existing assertion on it cannot start failing."""
        inbound, _onward = connecting_itinerary
        result = await run_connection(session=session, target_refs=[f"flight:{inbound.id}"])

        assert PAYLOAD_KEY not in result.payload
        assert not [ref for ref in result.evidence_refs if ref.startswith("flight_status:")]

    async def test_the_overlay_never_rewrites_the_services_verdict(
        self, session, connecting_itinerary
    ):
        """Additive only: status, reason and counts are the service's, not the adapter's."""
        from app.models.enums import ActionStatus
        from app.orchestrator.flight_status_adapter import FlightStatusOverlay
        from app.services.base import ServiceResult

        original = ServiceResult(
            status=ActionStatus.success,
            reason="7 itineraries no longer feasible",
            payload={"at_risk_count": 7, "rule_version": "v1"},
            evidence_refs=["booking:1"],
            provenance_kind="synthetic",
        )
        overlay = FlightStatusOverlay(mode="live", consulted=True, applied={1: 30}, replaced={1: 0})

        merged = merge_into_result(original, overlay)

        assert merged.status is original.status
        assert merged.reason == original.reason
        assert merged.payload["at_risk_count"] == 7
        assert merged.payload["rule_version"] == "v1"
        assert merged.provenance_kind == "synthetic"
        assert merged.payload[PAYLOAD_KEY]["applied"] == {"1": 30}
        assert "booking:1" in merged.evidence_refs

    async def test_an_empty_flight_scope_is_still_refused_by_the_existing_path(self, session):
        result = await run_connection(session=session, target_refs=[])

        assert result.payload["reason_code"] == "SERVICE_INPUTS_UNAVAILABLE"
        assert PAYLOAD_KEY not in result.payload
