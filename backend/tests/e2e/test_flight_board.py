"""The flight board must agree with the database, and must not invent what nobody assessed.

This board is what the Scenario Builder resolves a designator into: it supplies the `flight_id`
and `delay_minutes` that `POST /scenarios` then validates against the real `flight` table. While
it was served from `fixtures/api/flights.json` the two could not agree, and on the committed
dataset they did not — one offered flight did not exist in the database (`404` on submit) and
another published `delay_minutes: 0` where the database derives `65` (`422` on submit). Two of the
four selectable flights were unsubmittable, and the failure looked like a bug in the Scenario API
rather than a stale fixture.

So the first two tests here are the regression that would have caught it: every offered flight
exists, and every offered flight is actually submittable.

The rest pin the other half of the contract — that the board reports only figures the system
genuinely holds. `risk_index` without a `Prediction` and `connections_at_risk` without a
connection check are `null`, never `0`, because zero is a measurement and absence is not.

Owner: Stream A.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models
from app.db.base import Base
from app.db.scenario_queries import _flight_delay_minutes
from app.db.seed import seed_demo_dataset
from app.db.session import get_session
from app.main import app
from app.models.enums import ProvenanceKind, TriggerType
from app.models.reference import Flight, WeatherObservation
from app.models.workflow import Incident, Prediction

PREFIX = "/api/v1"


@pytest.fixture
async def board_engine() -> AsyncIterator:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def board_sessions(board_engine):
    factory = async_sessionmaker(bind=board_engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        await seed_demo_dataset(session)
        await session.commit()
    return factory


@pytest.fixture
def board_client(board_sessions) -> AsyncIterator[TestClient]:
    """The real app over the seeded dataset. Only the session dependency is overridden."""

    async def override() -> AsyncIterator:
        async with board_sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = override
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client
    app.dependency_overrides.pop(get_session, None)


def _board(client) -> dict:
    response = client.get(f"{PREFIX}/flights")
    assert response.status_code == 200, response.text
    return response.json()


async def _db_flights(sessions) -> dict[int, Flight]:
    async with sessions() as session:
        rows = (await session.execute(select(Flight))).scalars().all()
    return {row.id: row for row in rows}


# ------------------------------------------------------- the regression that was missing


class TestTheBoardAgreesWithTheDatabase:
    async def test_every_offered_flight_exists(self, board_client, board_sessions):
        """A board row for a flight that is not in the database is a 404 waiting to happen."""
        db = await _db_flights(board_sessions)
        offered = {row["id"] for row in _board(board_client)["flights"]}

        missing = sorted(offered - set(db))
        assert not missing, f"offered flight ids absent from the database: {missing}"

    async def test_every_offered_delay_matches_the_derivation_the_validator_uses(
        self, board_client, board_sessions
    ):
        """`POST /scenarios` refuses a declared delay that disagrees with recorded flight state.

        The board therefore has to publish the same derivation, and it does so by calling the same
        function rather than by keeping a second copy of the rule in step.
        """
        db = await _db_flights(board_sessions)
        offenders = []
        for row in _board(board_client)["flights"]:
            derived = _flight_delay_minutes(db[row["id"]])
            if row["delay_minutes"] != derived:
                offenders.append(
                    f"id={row['id']} {row['flight_number']}: board={row['delay_minutes']} "
                    f"derived={derived}"
                )
        assert not offenders, f"board disagrees with the database: {offenders}"

    async def test_the_whole_board_is_submittable_to_the_scenario_api(
        self, board_client, board_sessions
    ):
        """The end-to-end property, asserted by actually submitting.

        A board that merely looks consistent is not enough — the only proof that an operator can
        author a scenario over any offered flight is that the Scenario API accepts it.
        """
        rows = _board(board_client)["flights"]
        departures = [r for r in rows if r["origin_icao"] == "VOBL"]
        assert len(departures) >= 2, "expected several VOBL departures in the seeded dataset"

        members = [
            {
                "flight_id": row["id"],
                "role": "primary" if index == 0 else "affected_departure",
                "delay_minutes": row["delay_minutes"],
            }
            for index, row in enumerate(departures)
        ]
        response = board_client.post(
            f"{PREFIX}/scenarios",
            json={
                "root_cause": TriggerType.weather.value,
                "airport_icao": "VOBL",
                "severity": "high",
                "effective_at": datetime.now(tz=UTC).isoformat(),
                "actor_id": "board-test",
                "members": members,
            },
        )

        assert response.status_code == 201, response.text
        assert response.json()["scenario_reference"].startswith("SCN-")

    async def test_the_stale_fixture_row_is_gone(self, board_client):
        """`UK 864` existed only in the Wave-0 fixture and answered 404 on submit."""
        numbers = {row["flight_number"] for row in _board(board_client)["flights"]}
        assert "UK 864" not in numbers


# ----------------------------------------------------------- nothing is invented


class TestUnassessedValuesAreNullNotZero:
    async def test_risk_is_null_before_anything_is_scored(self, board_client):
        """Zero is a measurement. Absence is not, and must not read as "low risk"."""
        rows = _board(board_client)["flights"]

        assert rows, "expected a seeded board"
        for row in rows:
            assert row["risk_index"] is None, row["flight_number"]
            assert row["risk_level"] is None, row["flight_number"]

    async def test_connections_are_null_before_any_check_has_run(self, board_client):
        """`null` means nobody looked; `0` would mean somebody looked and found none."""
        for row in _board(board_client)["flights"]:
            assert row["connections_at_risk"] is None, row["flight_number"]

    async def test_a_recorded_prediction_is_surfaced_verbatim(self, board_client, board_sessions):
        """Once the work exists, the board reports it — and reports exactly it."""
        async with board_sessions() as session:
            flight = (await session.execute(select(Flight).where(Flight.id == 1))).scalar_one()
            session.add(
                Prediction(
                    flight_id=flight.id,
                    airport_icao="VOBL",
                    predicted_at=datetime.now(tz=UTC),
                    risk_index=83,
                    risk_level="severe",
                    rule_version="delay-risk-v1",
                    factors=[{"name": "visibility_marginal"}],
                    evidence_refs=["airport:VOBL"],
                )
            )
            await session.commit()

        row = next(r for r in _board(board_client)["flights"] if r["id"] == 1)
        assert row["risk_index"] == 83
        assert row["risk_level"] == "severe"
        # And a flight with no prediction of its own is still null, not backfilled from a sibling.
        other = next(r for r in _board(board_client)["flights"] if r["id"] == 2)
        assert other["risk_index"] is None

    async def test_passengers_is_a_real_count(self, board_client):
        """The seeder creates bookings on the affected flights and none on some others."""
        rows = {r["id"]: r for r in _board(board_client)["flights"]}

        assert rows[1]["passengers"] > 0
        assert all(isinstance(r["passengers"], int) for r in rows.values())

    async def test_an_incident_reference_appears_only_when_an_incident_exists(
        self, board_client, board_sessions
    ):
        before = {r["id"]: r["incident_reference"] for r in _board(board_client)["flights"]}
        assert all(reference is None for reference in before.values())

        async with board_sessions() as session:
            session.add(
                Incident(
                    reference="INC-BOARD-01",
                    flight_id=1,
                    trigger_type=TriggerType.weather,
                    severity="high",
                    opened_at=datetime.now(tz=UTC),
                    demo_dataset_id="bengaluru_storm",
                )
            )
            await session.commit()

        after = {r["id"]: r["incident_reference"] for r in _board(board_client)["flights"]}
        assert after[1] == "INC-BOARD-01"
        assert after[2] is None


# --------------------------------------------------- the network block and its provenance


class TestTheNetworkBlockReportsOnlyWhatWasObserved:
    async def test_only_airports_with_an_observation_appear(self, board_client, board_sessions):
        async with board_sessions() as session:
            observed = {
                str(icao)
                for (icao,) in (
                    await session.execute(
                        select(WeatherObservation.airport_icao).where(
                            WeatherObservation.is_forecast.is_(False)
                        )
                    )
                ).all()
            }

        reported = {row["airport_icao"] for row in _board(board_client)["network"]}
        assert reported == observed, f"reported={sorted(reported)} observed={sorted(observed)}"

    async def test_a_forecast_is_never_reported_as_current_conditions(
        self, board_client, board_sessions
    ):
        """The seeder stores TAF periods too. Presenting one as an observation is the leakage
        the `is_forecast` column exists to prevent, so an airport with only a forecast must not
        appear at all."""
        async with board_sessions() as session:
            session.add(
                WeatherObservation(
                    airport_icao="VAPO",
                    observed_at=datetime.now(tz=UTC) + timedelta(hours=2),
                    is_forecast=True,
                    wind_speed_kt=40,
                    visibility_m=500,
                    provenance_kind=ProvenanceKind.fixture,
                    provenance_provider="awc-fixture",
                    source_ref="taf:VAPO:test",
                )
            )
            await session.commit()

        reported = {row["airport_icao"] for row in _board(board_client)["network"]}
        assert "VAPO" not in reported

    async def test_each_airport_carries_its_own_observation_provenance(self, board_client):
        """This is how the console tells a live AWC reading from a replayed one.

        A live ingest stamps `real` / `awc`; the seeded archive stamps `fixture`. The board must
        pass that through rather than labelling the whole block one way.
        """
        for row in _board(board_client)["network"]:
            provenance = row["provenance"]
            assert provenance["kind"] in {"real", "fixture", "simulated", "synthetic"}
            assert provenance["provider"]
            assert provenance["source_ref"]

    async def test_a_live_observation_is_reported_as_real(self, board_client, board_sessions):
        """Written the way `ingest_live_weather` writes it, so the board proves the distinction."""
        async with board_sessions() as session:
            session.add(
                WeatherObservation(
                    airport_icao="VOBL",
                    observed_at=datetime.now(tz=UTC),
                    is_forecast=False,
                    wind_speed_kt=9,
                    wind_direction_deg=290,
                    visibility_m=6000,
                    ceiling_ft=None,
                    precipitation=None,
                    raw_metar="METAR VOBL 311830Z 29009KT 6000 FEW012 SCT080",
                    provenance_kind=ProvenanceKind.real,
                    provenance_provider="awc",
                    source_ref="metar:VOBL:live-test",
                )
            )
            await session.commit()

        vobl = next(r for r in _board(board_client)["network"] if r["airport_icao"] == "VOBL")
        assert vobl["provenance"]["kind"] == "real"
        assert vobl["provenance"]["provider"] == "awc"
        assert vobl["visibility_m"] == 6000
        # No ceiling is a real observation, not a missing one, and stays null rather than 0.
        assert vobl["ceiling_ft"] is None
        assert vobl["observation_age_minutes"] is not None

    async def test_airport_risk_is_null_until_something_is_scored_there(self, board_client):
        for row in _board(board_client)["network"]:
            assert row["risk_index"] is None
            assert row["risk_level"] is None
