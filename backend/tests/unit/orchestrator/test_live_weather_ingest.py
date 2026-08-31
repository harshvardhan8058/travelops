"""Live AWC observations entering the delay/risk pipeline, and changing what it computes.

Stream C's Aviation Weather Center provider has been complete since Phase 1 and had no caller:
`get_weather_provider()` was referenced by nothing outside its own tests, and every
`weather_observation` row came from the seeder, so `WEATHER_MODE=live` selected an implementation
that was never invoked. These tests cover the seam that closes that, and they are written against
the four ways this kind of ingest goes wrong quietly:

1. **A live badge over archived data.** The scored observation is chosen by the incident clock, so
   replaying a historical scenario in live mode legitimately scores an archived row. That is the
   leakage guard working — and it must be loudly recorded, not inferred from an absence.
2. **A failure that reads as calm weather.** A timeout or "no current METAR" must leave the ledger
   as it was and say so, never produce a blank observation that scores zero.
3. **A ledger that inflates.** `weather_observation` has no unique constraint, so a naive ingest
   writes the same METAR on every assessment and destroys the one thing the ledger is for.
4. **Fixture mode drifting.** `WEATHER_MODE=fixture` has to be untouched down to the absence of a
   journal entry, because Phase 2's whole verification runs through this code path.

The causality test drives real AWC METAR JSON through the real `LiveWeatherProvider`, the real
normaliser, the real ledger row, the real `load_delay_risk_inputs` selection and the real
`DelayRiskService`, against a real database. Only the socket is a stand-in — so it demonstrates
that a different live observation produces a different risk input rather than asserting it would.

Owner: Stream A (the seam) / Stream C (the provider, the normaliser and the models).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.config import Settings, WeatherMode
from app.models.enums import IncidentState, ProvenanceKind, TriggerType
from app.models.reference import Airport, Flight, Runway, WeatherObservation
from app.models.workflow import DecisionLog, Incident, Prediction
from app.orchestrator.engine import Orchestrator, WorkflowContext
from app.orchestrator.weather_adapter import (
    DETAIL_KEY,
    EVENT_LIVE_NOT_SCORED,
    EVENT_LIVE_UNAVAILABLE,
    ingest_live_weather,
)
from app.providers.base import ProviderError, ProviderErrorKind
from app.providers.weather import LiveWeatherProvider
from tests.unit.orchestrator.conftest import make_modes

#: The wall clock these tests reason from. An incident opened HERE has a current clock, which is
#: what a real detection looks like and the only situation in which a live reading is selectable.
NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


# ------------------------------------------------------------------------- AWC payloads


def _metar(
    *,
    icao: str = "VOBL",
    observed_at: datetime,
    wind_speed_kt: int | None = 8,
    wind_direction_deg: int | None = 270,
    visibility_statute_miles: object = "10+",
    clouds: list[dict] | None = None,
    raw: str = "VOBL 311200Z 27008KT 9999 FEW030 26/18 Q1008",
) -> dict:
    """One AWC METAR row, shaped as the Data API actually returns it.

    Visibility arrives in **statute miles** and is converted at the provider boundary; passing
    metres here would silently produce a 1.6x error, which is exactly the class of mistake the
    shared normaliser exists to prevent.
    """
    return {
        "icaoId": icao,
        "obsTime": int(observed_at.timestamp()),
        "reportTime": observed_at.isoformat(),
        "wspd": wind_speed_kt,
        "wdir": wind_direction_deg,
        "visib": visibility_statute_miles,
        "clouds": clouds or [],
        "rawOb": raw,
        "wxString": None,
    }


#: Calm and clear: nothing crosses a single scoring band.
BENIGN = {
    "wind_speed_kt": 8,
    "wind_direction_deg": 270,
    "visibility_statute_miles": "10+",
    "clouds": [{"cover": "FEW", "base": 3000}],
    "raw": "VOBL 311200Z 27008KT 9999 FEW030 26/18 Q1008",
}

#: A thunderstorm with a low ceiling and a strong crosswind on 09/27.
STORM = {
    "wind_speed_kt": 30,
    "wind_direction_deg": 180,
    "visibility_statute_miles": 0.75,
    "clouds": [{"cover": "OVC", "base": 600}],
    "raw": "VOBL 311200Z 18030G45KT 1200 TSRA OVC006 24/23 Q1004",
}


class _AWC:
    """A mocked Aviation Weather Center, recording what was asked of it."""

    def __init__(self, row: dict | None, *, status_code: int = 200, error: Exception | None = None):
        self.row = row
        self.status_code = status_code
        self.error = error
        self.requests: list[str] = []

    def provider(self, *, now: datetime = NOW) -> LiveWeatherProvider:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(str(request.url))
            if self.error is not None:
                raise self.error
            if self.status_code != 200:
                return httpx.Response(self.status_code, json={"detail": "nope"})
            return httpx.Response(200, json=[self.row] if self.row else [])

        provider = LiveWeatherProvider(
            client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
        )
        # Freeze the provider's clock so `is_stale` cannot flip with the wall clock mid-test.
        provider._now = lambda: now  # type: ignore[method-assign]
        return provider


class _Failing:
    """A provider that fails the way the real one does: a typed `ProviderError`."""

    name = "awc"
    mode = "live"

    def __init__(self, kind: ProviderErrorKind, message: str) -> None:
        self.kind = kind
        self.message = message

    async def get_observation(self, airport_icao: str):
        raise ProviderError(self.kind, self.message, provider=self.name)


class _Exploding:
    """A provider with a bug in it. Must not take the incident down with it."""

    name = "awc"
    mode = "live"

    async def get_observation(self, airport_icao: str):
        raise RuntimeError("provider has a bug")


# --------------------------------------------------------------------------- the dataset


def _settings(**overrides) -> Settings:
    base = {"app_env": "test", "weather_mode": WeatherMode.live}
    return Settings(_env_file=None, **{**base, **overrides})


def _engine(session, *, weather: WeatherMode = WeatherMode.live) -> Orchestrator:
    modes = make_modes()
    modes.weather = weather
    return Orchestrator(
        session,
        settings=_settings(weather_mode=weather),
        modes=modes,
        now=lambda: NOW,
    )


@pytest.fixture
async def vobl(session):
    """VOBL with its two runways, one flight, and one incident opened NOW.

    `opened_at = NOW` is the load-bearing detail. The incident clock is what selects an
    observation, so an incident opened now is the only case in which a METAR observed now can be
    scored — which is precisely what a live detection is.
    """
    session.add(
        Airport(
            icao_code="VOBL",
            iata_code="BLR",
            name="Kempegowda International",
            city="Bengaluru",
            country="IN",
            latitude=13.198889,
            longitude=77.705556,
            source_ref="fixture:test",
        )
    )
    session.add_all(
        [
            Runway(
                airport_icao="VOBL",
                designator="09",
                heading_degrees_true=93,
                heading_source="designator_derived",
                is_active=True,
            ),
            Runway(
                airport_icao="VOBL",
                designator="27",
                heading_degrees_true=273,
                heading_source="designator_derived",
                is_active=True,
            ),
        ]
    )
    flight = Flight(
        flight_number="6E 2134",
        airline_code="6E",
        origin_icao="VOBL",
        destination_icao="VOBL",
        scheduled_departure=NOW + timedelta(minutes=30),
        scheduled_arrival=NOW + timedelta(hours=2),
        block_time_minutes=90,
        status="scheduled",
        is_domestic=True,
        provenance_kind=ProvenanceKind.fixture,
        source_ref="fixture:test:flight",
    )
    session.add(flight)
    await session.flush()
    incident = Incident(
        reference="INC-2026-0831-VOBL-01",
        flight_id=flight.id,
        trigger_type=TriggerType.weather,
        severity="high",
        state=IncidentState.assessing,
        opened_at=NOW,
        demo_dataset_id="bengaluru_storm",
    )
    session.add(incident)
    await session.commit()
    return flight, incident


def _ctx(incident: Incident, flight: Flight) -> WorkflowContext:
    return WorkflowContext(
        incident_id=incident.id,
        incident_reference=incident.reference,
        state=IncidentState.assessing,
        correlation_id="correlation-weather",
        flight_id=flight.id,
        trigger_type="weather",
    )


async def _observations(session, icao: str = "VOBL") -> list[WeatherObservation]:
    rows = (
        await session.execute(
            select(WeatherObservation)
            .where(WeatherObservation.airport_icao == icao)
            .order_by(WeatherObservation.observed_at)
        )
    ).scalars()
    return list(rows)


async def _events(session, event_type: str) -> list[DecisionLog]:
    rows = (
        await session.execute(select(DecisionLog).where(DecisionLog.event_type == event_type))
    ).scalars()
    return list(rows)


# ============================================== 1. the causality proof: live data moves the score


class TestADifferentLiveObservationChangesTheRiskInput:
    @pytest.mark.parametrize(
        ("weather", "expect_level", "expect_zero"),
        [(BENIGN, "low", True), (STORM, "severe", False)],
    )
    async def test_the_same_scenario_scores_differently_on_different_live_weather(
        self, session, vobl, monkeypatch, weather, expect_level, expect_zero
    ):
        """Same airport, same flight, same incident, same ruleset. Only the METAR differs.

        This is the whole claim of live weather ingest, end to end: AWC JSON -> normaliser ->
        ledger row -> the existing `as_of` selection -> `WeatherInput` -> `DelayRiskService` ->
        the persisted `Prediction`.
        """
        flight, incident = vobl
        engine = _engine(session)

        # The provider is replaced at the socket only, so the real provider, normaliser, ledger
        # write and selection rule all take part.
        assessment = await _score_with(
            session, engine, incident, flight, monkeypatch, weather=weather, minutes_ago=5
        )

        assert assessment is not None
        if expect_zero:
            assert assessment["risk_index"] == 0
        else:
            assert assessment["risk_index"] > 0
        assert assessment["risk_level"] == expect_level

    async def test_two_live_observations_produce_two_different_predictions(
        self, session, vobl, monkeypatch
    ):
        """The comparison in one test, so the difference cannot be an artefact of two setups.

        A benign METAR and a storm METAR are fed to the identical pipeline in sequence. The risk
        index, the risk level and the recorded factors must all differ, and both must be traceable
        to the live observation that produced them.
        """
        flight, incident = vobl
        engine = _engine(session)

        benign = await _score_with(
            session, engine, incident, flight, monkeypatch, weather=BENIGN, minutes_ago=20
        )
        storm = await _score_with(
            session, engine, incident, flight, monkeypatch, weather=STORM, minutes_ago=2
        )

        assert benign["risk_index"] != storm["risk_index"], (
            "two materially different live observations produced the same risk index, so the "
            "observation is not reaching the calculation"
        )
        assert benign["risk_index"] == 0
        assert storm["risk_index"] > 74, "a thunderstorm with a 600ft ceiling must score severe"
        assert benign["risk_level"] == "low"
        assert storm["risk_level"] == "severe"

        # Both scores were computed from a live row, and the newer one won.
        predictions = (await session.execute(select(Prediction).order_by(Prediction.id))).scalars()
        recorded = list(predictions)
        assert len(recorded) == 2
        assert recorded[0].risk_index == 0
        assert recorded[1].risk_index == storm["risk_index"]
        assert recorded[1].risk_level == "severe"

    async def test_the_scored_prediction_cites_the_live_observation(
        self, session, vobl, monkeypatch
    ):
        """A number computed from live data has to be traceable to the reading that produced it."""
        flight, incident = vobl
        engine = _engine(session)
        observed_at = NOW - timedelta(minutes=5)

        await _score_with(
            session, engine, incident, flight, monkeypatch, weather=STORM, minutes_ago=5
        )

        prediction = (await session.execute(select(Prediction))).scalars().first()
        assert prediction is not None
        expected = f"observation:metar:VOBL:{observed_at.isoformat()}"
        assert expected in prediction.evidence_refs, prediction.evidence_refs


async def _score_with(
    session, engine, incident, flight, monkeypatch, *, weather: dict, minutes_ago: int
) -> dict:
    """Run one assessment against a live METAR observed `minutes_ago`."""
    awc = _AWC(_metar(observed_at=NOW - timedelta(minutes=minutes_ago), **weather))
    monkeypatch.setattr(
        "app.providers.weather.get_weather_provider", lambda _mode=None: awc.provider()
    )
    assessment = await engine._assess_delay_risk(_ctx(incident, flight))
    await session.commit()
    assert assessment is not None
    return assessment


# ======================================================== 2. provenance survives into the ledger


class TestProvenanceDistinguishesLiveFromFixture:
    async def test_a_live_row_is_stamped_real_and_names_awc(self, session, vobl):
        awc = _AWC(_metar(observed_at=NOW - timedelta(minutes=5), **STORM))

        outcome = await ingest_live_weather(
            session, "VOBL", as_of=NOW, settings=_settings(), provider=awc.provider()
        )
        await session.commit()

        assert outcome.persisted is True
        assert outcome.provenance_kind == ProvenanceKind.real.value
        assert outcome.provider == "awc"
        assert outcome.source_ref == f"metar:VOBL:{(NOW - timedelta(minutes=5)).isoformat()}"

        rows = await _observations(session)
        assert len(rows) == 1
        assert str(rows[0].provenance_kind) == ProvenanceKind.real.value
        assert rows[0].provenance_provider == "awc"
        assert rows[0].is_forecast is False

    async def test_the_row_matches_the_shape_the_seeder_writes(self, session, vobl):
        """A live row and a seeded one must be indistinguishable in structure, only in origin.

        Every existing read path — the risk loader, the gate's freshness check, the incident
        payload — reads these columns without knowing which produced the row, so a live row that
        omitted one would degrade a downstream answer rather than fail visibly.
        """
        awc = _AWC(_metar(observed_at=NOW - timedelta(minutes=5), **STORM))

        await ingest_live_weather(
            session, "VOBL", as_of=NOW, settings=_settings(), provider=awc.provider()
        )
        await session.commit()

        row = (await _observations(session))[0]
        assert row.wind_speed_kt == 30
        assert row.wind_direction_deg == 180
        # 0.75 statute miles, rounded to the nearest 100 metres.
        assert row.visibility_m == 1200
        assert row.ceiling_ft == 600
        assert row.precipitation == "thunderstorm"
        assert row.raw_metar is not None
        assert row.source_ref is not None

    async def test_the_journal_records_which_observation_was_scored(self, session, vobl):
        """The score alone cannot say whether this was live data, the first question anyone asks."""
        flight, incident = vobl
        awc = _AWC(_metar(observed_at=NOW - timedelta(minutes=5), **STORM))
        engine = _engine(session)

        await _ingest_and_assess(session, engine, incident, flight, awc)

        scored = await _events(session, "HIGH_RISK_DELAY") + await _events(
            session, "DELAY_RISK_SCORED"
        )
        assert scored, "the risk assessment was not journalled"
        detail = scored[-1].detail or {}
        source = detail["weather_source"]
        assert source["mode"] == "live"
        assert source["provenance_kind"] == ProvenanceKind.real.value
        assert source["source_ref"] == f"metar:VOBL:{(NOW - timedelta(minutes=5)).isoformat()}"
        assert detail[DETAIL_KEY]["persisted"] is True
        assert detail[DETAIL_KEY]["within_incident_clock"] is True


async def _ingest_and_assess(session, engine, incident, flight, awc, *, monkeypatch=None):
    import app.providers.weather as weather_pkg

    original = weather_pkg.get_weather_provider
    weather_pkg.get_weather_provider = lambda _mode=None: awc.provider()  # type: ignore[assignment]
    try:
        result = await engine._assess_delay_risk(_ctx(incident, flight))
        await session.commit()
        return result
    finally:
        weather_pkg.get_weather_provider = original  # type: ignore[assignment]


# ======================================================= 3. failure degrades safely and visibly


class TestLiveFailuresDegradeSafelyAndVisibly:
    @pytest.mark.parametrize(
        ("kind", "message"),
        [
            (ProviderErrorKind.timeout, "AWC did not respond within 8.0s"),
            (ProviderErrorKind.rate_limited, "AWC rate limit reached"),
            (ProviderErrorKind.unavailable, "AWC has no current METAR for VOBL"),
            (ProviderErrorKind.invalid_response, "AWC METAR response is not a list"),
            (ProviderErrorKind.forbidden, "AWC refused the request"),
        ],
    )
    async def test_no_row_is_written_and_the_reason_is_recorded(self, session, vobl, kind, message):
        outcome = await ingest_live_weather(
            session, "VOBL", as_of=NOW, settings=_settings(), provider=_Failing(kind, message)
        )
        await session.commit()

        assert outcome.consulted is True
        assert outcome.retrieved is False
        assert outcome.persisted is False
        assert outcome.provenance_kind == ProvenanceKind.unavailable.value
        assert kind.value in (outcome.reason or "")
        assert message in (outcome.reason or "")
        assert await _observations(session) == []

    async def test_an_http_error_from_the_real_provider_is_typed_not_raised(self, session, vobl):
        awc = _AWC(None, status_code=500)

        outcome = await ingest_live_weather(
            session, "VOBL", as_of=NOW, settings=_settings(), provider=awc.provider()
        )

        assert outcome.retrieved is False
        assert "unavailable" in (outcome.reason or "")

    async def test_no_current_metar_is_not_treated_as_clear_weather(self, session, vobl):
        """The real VAPO case: a station that files a TAF but no METAR.

        A blank reading here would look like calm, clear conditions and score zero — the single
        most dangerous failure mode this pipeline has.
        """
        awc = _AWC(None)

        outcome = await ingest_live_weather(
            session, "VOBL", as_of=NOW, settings=_settings(), provider=awc.provider()
        )

        assert outcome.retrieved is False
        assert await _observations(session) == []

    async def test_a_provider_bug_does_not_fail_the_incident(self, session, vobl):
        """`_assess_delay_risk` has no recovery handler around it.

        An unexpected fault in an enrichment must not abort the assessment of a real disruption,
        so it is caught and named rather than allowed to propagate.
        """
        outcome = await ingest_live_weather(
            session, "VOBL", as_of=NOW, settings=_settings(), provider=_Exploding()
        )

        assert outcome.consulted is True
        assert outcome.retrieved is False
        assert "RuntimeError" in (outcome.reason or "")

    async def test_the_assessment_still_completes_from_the_ledger(self, session, vobl):
        """A failed live lookup falls back to the ledger, and the fallback is journalled."""
        flight, incident = vobl
        session.add(
            WeatherObservation(
                airport_icao="VOBL",
                observed_at=NOW - timedelta(hours=1),
                is_forecast=False,
                wind_speed_kt=8,
                wind_direction_deg=270,
                visibility_m=9999,
                ceiling_ft=None,
                precipitation=None,
                raw_metar="archived",
                provenance_kind=ProvenanceKind.fixture,
                provenance_provider="awc-fixture",
                source_ref="fixture:archived:metar:VOBL",
            )
        )
        await session.commit()
        engine = _engine(session)

        import app.providers.weather as weather_pkg

        original = weather_pkg.get_weather_provider
        weather_pkg.get_weather_provider = lambda _mode=None: _Failing(  # type: ignore[assignment]
            ProviderErrorKind.timeout, "AWC did not respond"
        )
        try:
            assessment = await engine._assess_delay_risk(_ctx(incident, flight))
            await session.commit()
        finally:
            weather_pkg.get_weather_provider = original  # type: ignore[assignment]

        assert assessment is not None, "a live failure must not abandon the assessment"
        unavailable = await _events(session, EVENT_LIVE_UNAVAILABLE)
        assert len(unavailable) == 1
        detail = unavailable[0].detail or {}
        assert detail["scored_provenance_kind"] == ProvenanceKind.fixture.value
        assert "timeout" in detail[DETAIL_KEY]["reason"]

    async def test_a_live_failure_with_an_empty_ledger_still_reports_both_facts(
        self, session, vobl
    ):
        """No live reading AND no archived one: the existing unavailable route, better explained."""
        flight, incident = vobl
        engine = _engine(session)

        import app.providers.weather as weather_pkg

        original = weather_pkg.get_weather_provider
        weather_pkg.get_weather_provider = lambda _mode=None: _Failing(  # type: ignore[assignment]
            ProviderErrorKind.timeout, "AWC did not respond"
        )
        try:
            assessment = await engine._assess_delay_risk(_ctx(incident, flight))
            await session.commit()
        finally:
            weather_pkg.get_weather_provider = original  # type: ignore[assignment]

        assert assessment is None
        entries = await _events(session, "DELAY_RISK_UNAVAILABLE")
        assert len(entries) == 1
        detail = entries[0].detail or {}
        assert "timeout" in detail[DETAIL_KEY]["reason"]


# ============================== 4. live is never silently swapped for archived data


class TestLiveIsNeverSilentlySubstituted:
    async def test_a_historical_incident_records_that_live_was_not_scored(self, session, vobl):
        """Replaying the past in live mode is legitimate, and must be loud.

        The incident clock is in the past, so a METAR observed now is in its future and the
        existing rule correctly declines it. Without this entry, a live-badged run would appear to
        have reasoned from live data when it reasoned from the archive.
        """
        flight, incident = vobl
        incident.opened_at = NOW - timedelta(days=11)
        session.add(
            WeatherObservation(
                airport_icao="VOBL",
                observed_at=NOW - timedelta(days=11, minutes=10),
                is_forecast=False,
                wind_speed_kt=30,
                wind_direction_deg=180,
                visibility_m=1200,
                ceiling_ft=600,
                precipitation="thunderstorm",
                raw_metar="archived storm",
                provenance_kind=ProvenanceKind.fixture,
                provenance_provider="awc-fixture",
                source_ref="fixture:bengaluru_storm:metar:VOBL",
            )
        )
        await session.commit()

        awc = _AWC(_metar(observed_at=NOW - timedelta(minutes=5), **BENIGN))
        engine = _engine(session)
        assessment = await _ingest_and_assess(session, engine, incident, flight, awc)

        assert assessment is not None
        # The archived storm was scored, not the benign live reading.
        assert assessment["risk_level"] == "severe"
        not_scored = await _events(session, EVENT_LIVE_NOT_SCORED)
        assert len(not_scored) == 1
        detail = not_scored[0].detail or {}
        assert detail[DETAIL_KEY]["within_incident_clock"] is False
        assert detail[DETAIL_KEY]["persisted"] is True
        assert detail["scored_provenance_kind"] == ProvenanceKind.fixture.value
        assert "historical" in detail["resolution"]

    async def test_the_live_reading_is_still_recorded_even_when_not_scored(self, session, vobl):
        """Retrieved data is ledger data. Discarding it would lose the observation entirely."""
        _flight, incident = vobl
        incident.opened_at = NOW - timedelta(days=11)
        await session.commit()
        awc = _AWC(_metar(observed_at=NOW - timedelta(minutes=5), **BENIGN))

        outcome = await ingest_live_weather(
            session,
            "VOBL",
            as_of=NOW - timedelta(days=11),
            settings=_settings(),
            provider=awc.provider(),
        )
        await session.commit()

        assert outcome.persisted is True
        assert outcome.within_incident_clock is False
        assert len(await _observations(session)) == 1

    async def test_a_current_incident_scores_live_and_emits_no_substitution_entry(
        self, session, vobl
    ):
        flight, incident = vobl
        awc = _AWC(_metar(observed_at=NOW - timedelta(minutes=5), **STORM))
        engine = _engine(session)

        await _ingest_and_assess(session, engine, incident, flight, awc)

        assert await _events(session, EVENT_LIVE_NOT_SCORED) == []
        assert await _events(session, EVENT_LIVE_UNAVAILABLE) == []

    async def test_the_observed_time_is_never_back_dated_to_fit_the_incident(self, session, vobl):
        """The evidence timestamp is what the whole audit trail is selected and aged against.

        Moving it so a live reading "fits" a replayed scenario would make the replay reproduce a
        number that was never true — which is worse than the reading not being used.
        """
        observed_at = NOW - timedelta(minutes=5)
        awc = _AWC(_metar(observed_at=observed_at, **BENIGN))

        await ingest_live_weather(
            session,
            "VOBL",
            as_of=NOW - timedelta(days=11),
            settings=_settings(),
            provider=awc.provider(),
        )
        await session.commit()

        row = (await _observations(session))[0]
        stored = row.observed_at
        if stored.tzinfo is None:
            stored = stored.replace(tzinfo=UTC)
        assert stored == observed_at


# ================================================== 5. the ledger does not inflate


class TestTheLedgerStaysHonestAboutHowManyObservationsItSaw:
    async def test_the_same_metar_is_not_written_twice(self, session, vobl):
        """`weather_observation` has no unique constraint, so this is the only guard."""
        awc = _AWC(_metar(observed_at=NOW - timedelta(minutes=5), **STORM))

        first = await ingest_live_weather(
            session, "VOBL", as_of=NOW, settings=_settings(), provider=awc.provider()
        )
        await session.commit()
        second = await ingest_live_weather(
            session, "VOBL", as_of=NOW, settings=_settings(), provider=awc.provider()
        )
        await session.commit()

        assert first.persisted is True
        assert first.already_recorded is False
        assert second.persisted is False
        assert second.already_recorded is True
        # Not a failure: the observation is present and usable, it just was not new.
        assert second.retrieved is True
        assert second.reason is None
        count = await session.scalar(select(func.count()).select_from(WeatherObservation))
        assert count == 1

    async def test_a_newer_metar_is_a_new_row(self, session, vobl):
        for minutes in (30, 5):
            awc = _AWC(_metar(observed_at=NOW - timedelta(minutes=minutes), **BENIGN))
            await ingest_live_weather(
                session, "VOBL", as_of=NOW, settings=_settings(), provider=awc.provider()
            )
            await session.commit()

        rows = await _observations(session)
        assert len(rows) == 2

    async def test_a_repeated_assessment_does_not_grow_the_ledger(self, session, vobl):
        flight, incident = vobl
        awc = _AWC(_metar(observed_at=NOW - timedelta(minutes=5), **STORM))
        engine = _engine(session)

        await _ingest_and_assess(session, engine, incident, flight, awc)
        await _ingest_and_assess(session, engine, incident, flight, awc)

        assert len(await _observations(session)) == 1


# ================================================ 6. unknown stations and fixture mode


class TestBoundaries:
    async def test_an_airport_outside_the_reference_data_is_refused_not_inserted(self, session):
        """`weather_observation.airport_icao` is a foreign key.

        AWC will answer for stations this dataset has never heard of, and the insert would raise
        an `IntegrityError` from inside the assessing step — failing an incident over a missing
        enrichment.
        """
        awc = _AWC(_metar(icao="KJFK", observed_at=NOW - timedelta(minutes=5), **BENIGN))

        outcome = await ingest_live_weather(
            session, "KJFK", as_of=NOW, settings=_settings(), provider=awc.provider()
        )
        await session.commit()

        assert outcome.consulted is False
        assert outcome.persisted is False
        assert "not in the airport reference data" in (outcome.reason or "")
        assert awc.requests == [], "no lookup should be made for a station we cannot record"
        assert await _observations(session, "KJFK") == []

    async def test_fixture_mode_consults_nothing_and_writes_nothing(self, session, vobl):
        """`WEATHER_MODE=fixture` must be untouched — Phase 2 runs entirely through this path."""
        outcome = await ingest_live_weather(
            session, "VOBL", as_of=NOW, settings=_settings(weather_mode=WeatherMode.fixture)
        )
        await session.commit()

        assert outcome.consulted is False
        assert outcome.mode == "fixture"
        assert outcome.persisted is False
        assert outcome.reason is None
        assert await _observations(session) == []

    async def test_fixture_mode_adds_no_journal_entry_at_all(self, session, vobl):
        """Not merely "no extra events" — the risk entry's own detail must stay recognisable."""
        flight, incident = vobl
        session.add(
            WeatherObservation(
                airport_icao="VOBL",
                observed_at=NOW - timedelta(minutes=30),
                is_forecast=False,
                wind_speed_kt=8,
                wind_direction_deg=270,
                visibility_m=9999,
                ceiling_ft=None,
                precipitation=None,
                raw_metar="archived",
                provenance_kind=ProvenanceKind.fixture,
                provenance_provider="awc-fixture",
                source_ref="fixture:archived:metar:VOBL",
            )
        )
        await session.commit()
        engine = _engine(session, weather=WeatherMode.fixture)

        assessment = await engine._assess_delay_risk(_ctx(incident, flight))
        await session.commit()

        assert assessment is not None
        assert await _events(session, EVENT_LIVE_NOT_SCORED) == []
        assert await _events(session, EVENT_LIVE_UNAVAILABLE) == []
        scored = await _events(session, "DELAY_RISK_SCORED")
        detail = scored[-1].detail or {}
        assert detail[DETAIL_KEY]["consulted"] is False
        assert detail["weather_source"]["provenance_kind"] == ProvenanceKind.fixture.value

    async def test_the_provider_staleness_verdict_is_carried_even_though_the_ledger_has_no_column(
        self, session, vobl
    ):
        """`weather_observation` cannot store `is_stale`, so the record is the only place it lives.

        A three-hour-old METAR is past the provider's 90-minute ceiling. The gate's freshness check
        is the enforcer; this only ensures the verdict is not lost on the way past.
        """
        awc = _AWC(_metar(observed_at=NOW - timedelta(hours=3), **BENIGN))

        outcome = await ingest_live_weather(
            session, "VOBL", as_of=NOW, settings=_settings(), provider=awc.provider()
        )

        assert outcome.is_stale is True
        assert outcome.as_detail()["is_stale"] is True
