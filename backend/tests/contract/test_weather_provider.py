"""Weather provider contract — asserted against BOTH implementations.

The point of a provider Protocol is that the caller cannot tell which implementation it got.
So almost every test here is parametrised over the live and fixture providers and asserts the
same shape from each. The live provider is driven through a mock transport rather than the
network, so this file never needs the internet.

The unit assertions are the ones that matter most. AWC reports `visib` in **statute miles**
while METAR reports metres — VOBL's 8000 m visibility arrives as `4.97` — and storing that as
metres would turn a clear evening into an 800 m fog and drive a severe risk index. Nothing
downstream could detect it, so it is pinned here against the real `rawOb` strings in the
archived snapshot.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from data.loaders.ourairports import AIRPORT_ICAOS

from app.config import WeatherMode
from app.models.enums import ProvenanceKind
from app.providers.base import (
    ProviderError,
    ProviderErrorKind,
    ProviderHealth,
    WeatherProvider,
    WeatherReading,
)
from app.providers.weather import get_weather_provider
from app.providers.weather.fixture import SNAPSHOT_FILE, FixtureWeatherProvider, load_snapshot
from app.providers.weather.live import LiveWeatherProvider
from app.providers.weather.normalise import (
    METRES_PER_STATUTE_MILE,
    ceiling_ft_from_clouds,
    knots_from_kmh,
    knots_from_knots,
    observation_age_minutes,
    precipitation_from_text,
    visibility_m_from_statute_miles,
    wind_direction_from_awc,
)

FROZEN_NOW = datetime(2026, 8, 21, 10, 30, tzinfo=UTC)


@pytest.fixture(scope="module")
def snapshot() -> dict:
    return load_snapshot(SNAPSHOT_FILE)


def _mock_live(snapshot: dict) -> LiveWeatherProvider:
    """A live provider whose transport replays the archived payloads.

    Exercises the real HTTP code path — URL building, status handling, JSON decode — without
    a network, so the contract holds for both providers in CI.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        ids = request.url.params.get("ids", "")
        wanted = set(ids.split(","))
        key = "metar" if request.url.path.endswith("/metar") else "taf"
        rows = [row for row in snapshot[key] if row.get("icaoId") in wanted]
        return httpx.Response(200, json=rows)

    transport = httpx.MockTransport(handler)
    return LiveWeatherProvider(client=httpx.AsyncClient(transport=transport))


@pytest.fixture
def providers(snapshot: dict) -> dict[str, WeatherProvider]:
    return {
        "live": _mock_live(snapshot),
        "fixture": FixtureWeatherProvider(now=FROZEN_NOW, inject_scenario=False),
    }


BOTH = ["live", "fixture"]


# ------------------------------------------------------------------ protocol conformance


@pytest.mark.parametrize("mode", BOTH)
def test_both_implementations_satisfy_the_protocol(providers, mode):
    assert isinstance(providers[mode], WeatherProvider)


def test_selection_is_by_config_not_by_import_site():
    assert isinstance(get_weather_provider(WeatherMode.live), LiveWeatherProvider)
    assert isinstance(get_weather_provider(WeatherMode.fixture), FixtureWeatherProvider)


def test_unknown_mode_raises_rather_than_defaulting():
    """Guessing which weather source is in use is the ambiguity provenance exists to remove."""
    with pytest.raises(ValueError, match="unknown weather mode"):
        get_weather_provider("satellite")  # type: ignore[arg-type]


# -------------------------------------------------------------------- identical shapes


@pytest.mark.parametrize("mode", BOTH)
async def test_observation_shape_is_identical_across_modes(providers, mode):
    reading = await providers[mode].get_observation("VOBL")
    assert isinstance(reading, WeatherReading)
    assert reading.airport_icao == "VOBL"
    assert set(reading.model_dump()) == {
        "airport_icao",
        "observed_at",
        "wind_speed_kt",
        "wind_direction_deg",
        "visibility_m",
        "ceiling_ft",
        "precipitation",
        "raw_metar",
        "provenance",
    }


@pytest.mark.parametrize("mode", BOTH)
async def test_forecast_returns_readings_in_the_same_shape(providers, mode):
    readings = await providers[mode].get_forecast("VOBL")
    assert readings
    for reading in readings:
        assert isinstance(reading, WeatherReading)
        assert reading.airport_icao == "VOBL"


@pytest.mark.parametrize("mode", BOTH)
async def test_both_modes_agree_on_the_normalised_values(providers, mode):
    """Same snapshot through both code paths must give the same physics."""
    reading = await providers[mode].get_observation("VOHS")
    assert reading.wind_speed_kt == 12
    assert reading.wind_direction_deg == 320
    assert reading.visibility_m == 6000
    assert reading.ceiling_ft == 2500


@pytest.mark.parametrize("mode", BOTH)
async def test_health_reports_and_never_raises(providers, mode):
    health = await providers[mode].health()
    assert isinstance(health, ProviderHealth)
    assert health.healthy is True
    assert health.mode == mode


# ------------------------------------------------------------------------- provenance


@pytest.mark.parametrize("mode", BOTH)
async def test_every_reading_carries_provenance(providers, mode):
    reading = await providers[mode].get_observation("VOBL")
    stamp = reading.provenance
    assert stamp.provider
    assert stamp.observed_at is not None
    assert stamp.retrieved_at is not None
    assert stamp.source_ref and stamp.source_ref.startswith("metar:")


async def test_live_readings_are_labelled_real(providers):
    reading = await providers["live"].get_observation("VOBL")
    assert reading.provenance.kind is ProvenanceKind.real


async def test_fixture_readings_are_never_labelled_real(providers):
    """The archived bytes came from a real source, but a replay is not an observation of
    current conditions. Every UI badge derives from this field."""
    reading = await providers["fixture"].get_observation("VOBL")
    assert reading.provenance.kind is ProvenanceKind.fixture


async def test_observation_age_is_derivable_from_the_stamp(providers):
    reading = await providers["fixture"].get_observation("VOBL")
    age = observation_age_minutes(
        observed_at=reading.provenance.observed_at, now=reading.provenance.retrieved_at
    )
    assert age == 30  # observed 10:00Z, snapshot clock 10:30Z


async def test_stale_observations_are_flagged(snapshot):
    """A three-hour-old METAR must not be presented as current conditions."""
    provider = FixtureWeatherProvider(now=FROZEN_NOW + timedelta(hours=3), inject_scenario=False)
    reading = await provider.get_observation("VOBL")
    assert reading.provenance.is_stale is True


async def test_fresh_observations_are_not_flagged(providers):
    reading = await providers["fixture"].get_observation("VOBL")
    assert reading.provenance.is_stale is False


async def test_forecasts_are_distinguishable_from_observations(providers):
    """Scoring a TAF as though it were a METAR is a documented leakage bug."""
    readings = await providers["fixture"].get_forecast("VOBL")
    assert all(r.provenance.source_ref.startswith("taf:") for r in readings)
    assert all(not r.provenance.is_stale for r in readings)


# ------------------------------------------------------------------- units at the boundary


@pytest.mark.parametrize(
    ("icao", "expected_visibility_m"),
    [
        # Every value below is cross-checked against the rawOb string in the snapshot.
        ("VOBL", 8000),  # visib 4.97 SM  -> rawOb "8000"
        ("VIDP", 6000),  # visib 3.73 SM  -> rawOb "6000"
        ("VAAH", 3500),  # visib 2.17 SM  -> rawOb "3500"
        ("VABB", 4000),  # visib 2.49 SM  -> rawOb "4000"
        ("VOCI", 5000),  # visib 3.11 SM  -> rawOb "5000"
    ],
)
@pytest.mark.parametrize("mode", BOTH)
async def test_statute_miles_are_converted_to_metres(providers, mode, icao, expected_visibility_m):
    reading = await providers[mode].get_observation(icao)
    assert reading.visibility_m == expected_visibility_m


@pytest.mark.parametrize("mode", BOTH)
async def test_converted_visibility_matches_the_raw_metar_string(providers, mode):
    """The strongest available check: the station published the metric value itself."""
    for icao in ("VOBL", "VIDP", "VAAH", "VABB", "VOCI", "VOGO", "VOHS", "VOMM"):
        reading = await providers[mode].get_observation(icao)
        assert reading.raw_metar
        published = reading.raw_metar.split()
        assert f"{reading.visibility_m:04d}" in published, (
            f"{icao}: normalised {reading.visibility_m} m is not in {reading.raw_metar}"
        )


@pytest.mark.parametrize("mode", BOTH)
async def test_wind_speed_matches_the_raw_metar_knots(providers, mode):
    """AWC `wspd` is already knots. Pinned against the KT group in the raw string so a
    future km/h source cannot be wired in silently."""
    for icao in ("VOBL", "VIDP", "VABB", "VOHS"):
        reading = await providers[mode].get_observation(icao)
        group = next(part for part in reading.raw_metar.split() if part.endswith("KT"))
        assert int(group[3:5]) == reading.wind_speed_kt


def test_kmh_conversion_exists_and_is_correct():
    """45 km/h is 24 kt, not 45 kt. The exact case docs/12 warns about."""
    assert knots_from_kmh(45) == 24
    assert knots_from_knots(45) == 45


def test_statute_mile_constant_is_exact():
    assert METRES_PER_STATUTE_MILE == 1609.344


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (4.97, 8000),
        ("10+", 16100),
        ("6+", 9700),
        (0, 0),
        (None, None),
        ("", None),
        ("not-a-number", None),
        (-1, None),
    ],
)
def test_visibility_parsing_edge_cases(value, expected):
    assert visibility_m_from_statute_miles(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(320, 320), (0, 0), (360, 0), ("VRB", None), (None, None), ("270", 270)],
)
def test_wind_direction_parsing(value, expected):
    """Variable wind has no direction. Defaulting it to 0 would compute a crosswind against
    a northerly that was never reported."""
    assert wind_direction_from_awc(value) == expected


# ------------------------------------------------------------------------------ ceiling


@pytest.mark.parametrize(
    ("clouds", "expected"),
    [
        ([{"cover": "BKN", "base": 900}], 900),
        ([{"cover": "OVC", "base": 400}, {"cover": "BKN", "base": 1200}], 400),
        # SCT and FEW are not a ceiling. Treating them as one would flag half of monsoon
        # India as severe.
        ([{"cover": "SCT", "base": 1200}, {"cover": "FEW", "base": 800}], None),
        ([], None),
        (None, None),
        ([{"cover": "BKN", "base": None}], None),
    ],
)
def test_ceiling_is_the_lowest_broken_or_overcast_layer(clouds, expected):
    assert ceiling_ft_from_clouds(clouds) == expected


@pytest.mark.parametrize("mode", BOTH)
async def test_scattered_only_station_reports_no_ceiling(providers, mode):
    """VOBL is SCT012 SCT018 in the snapshot: no ceiling, which is not a ceiling of zero."""
    reading = await providers[mode].get_observation("VOBL")
    assert reading.ceiling_ft is None


@pytest.mark.parametrize("mode", BOTH)
async def test_broken_layer_station_reports_a_ceiling(providers, mode):
    reading = await providers[mode].get_observation("VAAH")
    assert reading.ceiling_ft == 8000  # BKN080


# ------------------------------------------------------------------------ precipitation


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("METAR VOBL 201536Z 25024KT 0800 RA BKN009", "rain"),
        ("... -SHRA ...", "showers"),
        ("... TSRA ...", "thunderstorm"),
        ("... DZ ...", "drizzle"),
        ("... SN ...", "snow"),
        # Obscurations reduce visibility, which is already scored. They are not precipitation
        # and counting them here would double-penalise the same condition.
        ("METAR VABB 211000Z 27015KT 4000 HZ SCT018", None),
        ("METAR VAAH 211000Z 20004KT 3500 BR FEW015", None),
        ("METAR VIDP 211000Z 32008KT 6000 SCT035", None),
        (None, None),
    ],
)
def test_precipitation_normalisation(text, expected):
    assert precipitation_from_text(text) == expected


@pytest.mark.parametrize("mode", BOTH)
async def test_haze_is_not_reported_as_precipitation(providers, mode):
    reading = await providers[mode].get_observation("VABB")
    assert "HZ" in reading.raw_metar
    assert reading.precipitation is None


# --------------------------------------------------------------------- the raw string


@pytest.mark.parametrize("mode", BOTH)
async def test_raw_metar_is_retained(providers, mode):
    """When a parser bug produces a nonsensical prediction, the original string is the only
    way to tell whether the data or the parse was wrong."""
    reading = await providers[mode].get_observation("VOBL")
    assert reading.raw_metar
    assert reading.raw_metar.startswith("METAR VOBL")


# ------------------------------------------------------------------------- typed errors


@pytest.mark.parametrize("mode", BOTH)
async def test_missing_station_is_unavailable_not_silent_success(providers, mode):
    """VAPO files a TAF but no METAR — a real gap, not a hypothetical. Returning a blank
    reading would look like calm, clear weather."""
    with pytest.raises(ProviderError) as exc:
        await providers[mode].get_observation("VAPO")
    assert exc.value.kind is ProviderErrorKind.unavailable


@pytest.mark.parametrize("mode", BOTH)
async def test_unknown_airport_is_unavailable(providers, mode):
    with pytest.raises(ProviderError) as exc:
        await providers[mode].get_observation("ZZZZ")
    assert exc.value.kind is ProviderErrorKind.unavailable


def _failing_live(handler) -> LiveWeatherProvider:
    return LiveWeatherProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


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
        await provider.get_observation("VOBL")
    assert exc.value.kind is expected
    assert exc.value.provider == "awc"


async def test_timeout_maps_to_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("too slow", request=request)

    with pytest.raises(ProviderError) as exc:
        await _failing_live(handler).get_observation("VOBL")
    assert exc.value.kind is ProviderErrorKind.timeout


async def test_connection_failure_maps_to_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    with pytest.raises(ProviderError) as exc:
        await _failing_live(handler).get_observation("VOBL")
    assert exc.value.kind is ProviderErrorKind.unavailable


@pytest.mark.parametrize("response", [httpx.Response(204), httpx.Response(200, content=b"")])
async def test_no_content_is_unavailable_not_invalid_response(response):
    """Observed against the real API, and originally missed because the mock returned `[]`.

    AWC answers a station it has no report for with **HTTP 204 and an empty body**, not with
    an empty JSON list. Mapping that to `invalid_response` would tell the orchestrator the
    provider is broken when the truth is that the station files no METAR — a different
    problem with a different fallback.
    """
    provider = _failing_live(lambda request: response)
    with pytest.raises(ProviderError) as exc:
        await provider.get_observation("VAPO")
    assert exc.value.kind is ProviderErrorKind.unavailable


async def test_malformed_body_maps_to_invalid_response():
    provider = _failing_live(lambda request: httpx.Response(200, text="<html>outage</html>"))
    with pytest.raises(ProviderError) as exc:
        await provider.get_observation("VOBL")
    assert exc.value.kind is ProviderErrorKind.invalid_response


async def test_non_list_body_maps_to_invalid_response():
    provider = _failing_live(lambda request: httpx.Response(200, json={"error": "nope"}))
    with pytest.raises(ProviderError) as exc:
        await provider.get_observation("VOBL")
    assert exc.value.kind is ProviderErrorKind.invalid_response


async def test_row_without_observation_time_is_invalid_response():
    provider = _failing_live(
        lambda request: httpx.Response(200, json=[{"icaoId": "VOBL", "wspd": 11}])
    )
    with pytest.raises(ProviderError) as exc:
        await provider.get_observation("VOBL")
    assert exc.value.kind is ProviderErrorKind.invalid_response


async def test_health_reports_down_instead_of_raising_on_failure():
    provider = _failing_live(lambda request: httpx.Response(503))
    health = await provider.health()
    assert health.healthy is False
    assert "unavailable" in health.detail


# ------------------------------------------------------------------- the demo scenario


async def test_injected_storm_is_served_from_the_snapshot():
    """The scenario the demo runs comes from the same place as the background weather rather
    than a special code path."""
    provider = FixtureWeatherProvider(now=FROZEN_NOW, inject_scenario=True)
    reading = await provider.get_observation("VOBL")

    assert reading.wind_speed_kt == 24
    assert reading.wind_direction_deg == 250
    assert reading.visibility_m == 800
    assert reading.ceiling_ft == 900
    assert reading.precipitation == "rain"
    assert reading.provenance.kind is ProvenanceKind.fixture
    assert reading.provenance.source_ref == "fixture:bengaluru_storm:metar:VOBL"


async def test_injected_storm_matches_the_scenario_fixture():
    """The provider and data/fixtures/bengaluru_storm.yaml must not drift apart."""
    import yaml

    from app.config import REPO_ROOT

    scenario = yaml.safe_load(
        (REPO_ROOT / "data" / "fixtures" / "bengaluru_storm.yaml").read_text(encoding="utf-8")
    )
    conditions = scenario["injected_conditions"]

    provider = FixtureWeatherProvider(now=FROZEN_NOW, inject_scenario=True)
    reading = await provider.get_observation(scenario["airport"])

    assert reading.wind_speed_kt == conditions["wind_speed_kt"]
    assert reading.wind_direction_deg == conditions["wind_direction_deg"]
    assert reading.visibility_m == conditions["visibility_m"]
    assert reading.ceiling_ft == conditions["ceiling_ft"]
    assert reading.precipitation == conditions["precipitation"]


async def test_injection_can_be_turned_off_for_background_weather():
    provider = FixtureWeatherProvider(now=FROZEN_NOW, inject_scenario=False)
    reading = await provider.get_observation("VOBL")
    assert reading.visibility_m == 8000  # the archived observation, not the storm


async def test_other_airports_are_unaffected_by_the_injection():
    provider = FixtureWeatherProvider(now=FROZEN_NOW, inject_scenario=True)
    reading = await provider.get_observation("VIDP")
    assert reading.visibility_m == 6000


# ------------------------------------------------------------------------- determinism


async def test_fixture_output_is_reproducible():
    """A fixture provider whose output moves with the wall clock is not a fixture, and
    `is_stale` would flip mid-demo."""
    first = await FixtureWeatherProvider(now=FROZEN_NOW).get_observation("VOBL")
    second = await FixtureWeatherProvider(now=FROZEN_NOW).get_observation("VOBL")
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


async def test_fixture_clock_defaults_to_the_snapshot_retrieval_time():
    reading = await FixtureWeatherProvider(inject_scenario=False).get_observation("VOBL")
    snapshot = load_snapshot(SNAPSHOT_FILE)
    assert reading.provenance.retrieved_at.isoformat() == snapshot["source"]["retrieved_at"]


# ---------------------------------------------------------------------------- the archive


def test_snapshot_records_its_source_and_licence(snapshot):
    source = snapshot["source"]
    assert source["provider"] == "awc"
    assert source["licence"] == "US Government work, public domain"
    assert len(source["metar_sha256"]) == 64
    assert len(source["taf_sha256"]) == 64
    assert source["metar_url"].startswith("https://aviationweather.gov/")


def test_snapshot_is_valid_json_on_disk():
    json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))


def test_snapshot_covers_the_airport_set(snapshot):
    """Nine of the ten file METARs; all ten file TAFs. VAPO's gap is real and reproduced."""
    metar_stations = {row["icaoId"] for row in snapshot["metar"]}
    taf_stations = {row["icaoId"] for row in snapshot["taf"]}

    assert taf_stations == set(AIRPORT_ICAOS)
    assert metar_stations == set(AIRPORT_ICAOS) - {"VAPO"}


def test_injected_block_declares_that_its_raw_metar_is_synthesised():
    """A constructed METAR string sitting beside captured ones must say which it is."""
    injected = load_snapshot(SNAPSHOT_FILE)["injected"]
    assert "SYNTHESISED" in injected["note"]
