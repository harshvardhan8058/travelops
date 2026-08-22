"""The seeded dataset: determinism, scoping, and the Stage 2 chain end to end.

Determinism is asserted against the pure plan, which needs no database. The round trip runs
on SQLite so it holds in CI; the same chain is verified against real Postgres locally, which
is where the `as_of` bug in `load_delay_risk_inputs` was found — SQLite would have reproduced
it, but only a run would have shown it.

`booking.cabin` is a reminder that SQLite is a stand-in and not the target: it ignores
`VARCHAR(12)` where Postgres rejects a 15-character value, so a generated `premium_economy`
passed here and failed there.
"""

from __future__ import annotations

import pytest
from data.generators.cascade_spec import BENGALURU_STORM
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.db.scenario_queries import (
    load_business_constraints,
    load_connection_inputs,
    load_crew_impact_inputs,
    load_delay_risk_inputs,
)
from app.db.seed import (
    DEMO_DATASET_ID,
    INCIDENT_GROUP_REFERENCE,
    TABLE_ORDER,
    build_seed_plan,
    dataset_counts,
    plan_digest,
    reset_demo_dataset,
    seed_demo_dataset,
)
from app.models.enums import ActionStatus, ProvenanceKind
from app.services.connection import ConnectionService
from app.services.crew_impact import CrewImpactService
from app.services.delay_risk import DelayRiskService
from tests.contract.sqlite_support import create_sqlite_engine

AFFECTED_FLIGHT_IDS = {flight.flight_id for flight in BENGALURU_STORM.affected_flights}

EXPECTED_COUNTS = {
    "airport": 10,
    "runway": 38,
    "weather_observation": 41,
    "flight": 42,
    "hotel": 11,
    # 7 from Phase 2: the passenger priority ruleset and the crew expansion bound both live
    # in data, so the policy that ranks passengers and the depth of the crew walk are
    # inspectable and versioned rather than compiled into a service.
    "business_constraint": 7,
    "crew_member": 24,
    "pairing": 9,
    "pairing_leg": 28,
    "crew_pairing_assignment": 24,
    "passenger": 604,
    "booking": 604,
    "booking_segment": 642,
    "incident_group": 1,
    # One row per affected flight. Eight, not seven: UK 705 arrives into VOBL, so a
    # departure-origin query would miss it and still report nine pairings.
    "incident_group_flight": 8,
}


@pytest.fixture(scope="module")
def plan() -> dict:
    return build_seed_plan()


# --------------------------------------------------------------------- determinism


def test_plan_is_byte_identical_across_builds():
    """`make seed` produces a byte-identical dataset for seed 20260807 — checkable, not
    aspirational: build it twice and compare the digest."""
    assert plan_digest(build_seed_plan()) == plan_digest(build_seed_plan())


def test_digest_is_sensitive_to_any_change(plan):
    tampered = {table: list(rows) for table, rows in plan.items()}
    tampered["passenger"][0] = {**tampered["passenger"][0], "tier": "platinum"}
    assert plan_digest(tampered) != plan_digest(plan)


def test_plan_row_counts_are_the_expected_dataset(plan):
    assert {table: len(rows) for table, rows in plan.items()} == EXPECTED_COUNTS


def test_plan_covers_every_table_in_insert_order(plan):
    assert list(plan) == list(TABLE_ORDER)


def test_every_row_has_an_explicit_id_except_airport(plan):
    """Explicit ids are what make `flight:1` mean the same thing in every environment."""
    for table, rows in plan.items():
        if table == "airport":
            assert all("icao_code" in row for row in rows)
            continue
        assert all("id" in row for row in rows), table
        ids = [row["id"] for row in rows]
        assert len(set(ids)) == len(ids), f"duplicate ids in {table}"


# ------------------------------------------------------------------------- honesty


def test_passengers_are_visibly_synthetic(plan):
    for row in plan["passenger"]:
        assert row["reference"].startswith("PAX-")
        assert row["email"].endswith("@example.com")
        assert row["provenance_kind"] is ProvenanceKind.synthetic


def test_no_passenger_email_is_routable(plan):
    """There is no code path that stores real personal data."""
    domains = {row["email"].split("@")[1] for row in plan["passenger"]}
    assert domains == {"example.com"}


def test_schedules_are_labelled_synthetic(plan):
    """AIKosh is not archived, so no flight may claim to be real."""
    for row in plan["flight"]:
        assert row["provenance_kind"] is ProvenanceKind.synthetic


def test_airports_and_runways_are_the_only_real_rows(plan):
    assert all(row["source_ref"].startswith("ourairports:") for row in plan["airport"])
    assert len(plan["runway"]) == 38


def test_archived_weather_is_labelled_fixture_not_real(plan):
    """The bytes came from a real source; a replay is not an observation of now."""
    for row in plan["weather_observation"]:
        assert row["provenance_kind"] is ProvenanceKind.fixture


def test_forecasts_are_separated_from_observations(plan):
    forecasts = [row for row in plan["weather_observation"] if row["is_forecast"]]
    actuals = [row for row in plan["weather_observation"] if not row["is_forecast"]]
    assert forecasts, "TAF rows must be seeded so the distinction is exercised"
    assert len(actuals) == 10  # nine archived METARs plus the injected storm
    for row in forecasts:
        assert row["source_ref"].startswith("taf:")


def test_the_injected_storm_observation_is_present(plan):
    storm = [
        row
        for row in plan["weather_observation"]
        if row["source_ref"] == "fixture:bengaluru_storm:metar:VOBL"
    ]
    assert len(storm) == 1
    assert storm[0]["wind_speed_kt"] == 24
    assert storm[0]["visibility_m"] == 800
    assert storm[0]["ceiling_ft"] == 900


def test_incident_group_is_tagged_with_the_dataset(plan):
    group = plan["incident_group"][0]
    assert group["demo_dataset_id"] == DEMO_DATASET_ID
    assert group["reference"] == INCIDENT_GROUP_REFERENCE


def test_no_rollup_columns_are_seeded(plan):
    """Counts are derived from rows. A stored total is a total nobody checks."""
    group = plan["incident_group"][0]
    for forbidden in ("flights_affected", "passengers_affected", "crew_rotations_affected"):
        assert forbidden not in group


# ------------------------------------------------------------------- the dataset shape


def test_passenger_count_matches_the_scenario(plan):
    assert len(plan["passenger"]) == 604
    assert sum(f.passengers for f in BENGALURU_STORM.affected_flights) == 604


def test_hotel_capacity_is_short_within_the_cap(plan):
    """`hotel_capacity_shortfall: true` in the scenario fixture is deliberate: a recovery
    where everything succeeds demonstrates nothing."""
    cap = next(
        row["constraint_value"]["inr"]
        for row in plan["business_constraint"]
        if row["constraint_key"] == "max_rate_inr"
    )
    per_room = next(
        row["constraint_value"]["count"]
        for row in plan["business_constraint"]
        if row["constraint_key"] == "passengers_per_room"
    )
    within_cap = sum(h["available_rooms"] for h in plan["hotel"] if h["rate_inr"] <= cap)
    needed = -(-BENGALURU_STORM.affected[0].flight.passengers // per_room)
    assert within_cap < needed


def test_some_hotels_exceed_the_cap_so_the_constraint_bites(plan):
    cap = next(
        row["constraint_value"]["inr"]
        for row in plan["business_constraint"]
        if row["constraint_key"] == "max_rate_inr"
    )
    assert any(h["rate_inr"] > cap for h in plan["hotel"])
    assert any(h["rate_inr"] <= cap for h in plan["hotel"])


def test_partner_and_non_partner_hotels_both_exist(plan):
    partners = {h["is_partner"] for h in plan["hotel"]}
    assert partners == {True, False}


def test_delay_risk_ruleset_is_seeded_as_data(plan):
    """No service holds a threshold literal; the numbers are a row."""
    row = next(row for row in plan["business_constraint"] if row["service"] == "delay_risk_service")
    assert row["constraint_key"] == "ruleset"
    assert row["constraint_value"]["version"] == "delay-risk-v1"
    assert row["is_hard"] is True


# --------------------------------------------------------------------- the round trip


@pytest.fixture
async def session(tmp_path):
    """A real schema on SQLite. Postgres is the target; this keeps CI honest without it."""
    engine = create_sqlite_engine(tmp_path / "seed.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as active:
        yield active
    await engine.dispose()


async def test_seed_lands_every_row(session):
    report = await seed_demo_dataset(session)
    await session.commit()
    assert report.counts == EXPECTED_COUNTS
    assert await dataset_counts(session) == EXPECTED_COUNTS


async def test_seeding_twice_does_not_duplicate(session):
    await seed_demo_dataset(session)
    await session.commit()
    first = await dataset_counts(session)

    await seed_demo_dataset(session)
    await session.commit()
    assert await dataset_counts(session) == first


async def test_reset_removes_the_dataset(session):
    await seed_demo_dataset(session)
    await session.commit()
    report = await reset_demo_dataset(session)
    await session.commit()

    assert sum(report.deleted.values()) >= sum(EXPECTED_COUNTS.values())
    assert all(count == 0 for count in (await dataset_counts(session)).values())


async def test_reset_is_scoped_and_does_not_truncate(session):
    """A TRUNCATE here would take an operator's own data with it."""
    from sqlalchemy import select

    from app.models.reference import Airport

    await seed_demo_dataset(session)
    session.add(
        Airport(
            icao_code="ZZZZ",
            iata_code="ZZZ",
            name="Not part of the demo dataset",
            city="Elsewhere",
            country="IN",
            latitude=0,
            longitude=0,
            timezone="Asia/Kolkata",
        )
    )
    await session.commit()

    await reset_demo_dataset(session)
    await session.commit()

    survivors = (await session.execute(select(Airport.icao_code))).scalars().all()
    assert survivors == ["ZZZZ"]


# ---------------------------------------------------- the Stage 2 chain, from the DB


async def test_delay_risk_from_the_seeded_database(session):
    """The regression that matters: assessed as of the scenario clock, this must be the
    storm and not the newer clear-weather archive."""
    await seed_demo_dataset(session)
    await session.commit()

    weather, runways, ruleset = await load_delay_risk_inputs(
        session, "VOBL", as_of=BENGALURU_STORM.injected_at
    )
    assert weather.visibility_m == 800
    assert weather.wind_speed_kt == 24
    assert weather.source_ref == "fixture:bengaluru_storm:metar:VOBL"
    assert len(runways) == 4
    assert ruleset.version == "delay-risk-v1"

    result = await DelayRiskService().execute(
        weather=weather, runways=runways, ruleset=ruleset, event_threshold=75
    )
    assert result.status is ActionStatus.success
    assert result.payload["risk_index"] >= 75
    assert result.payload["risk_level"] == "severe"
    assert result.payload["event_recommended"] is True


async def test_without_as_of_the_newer_archive_wins(session):
    """Documents why `as_of` exists: the archived observations are genuinely later than the
    scenario, and back-dating real data to fit would be the wrong fix."""
    await seed_demo_dataset(session)
    await session.commit()

    weather, _, _ = await load_delay_risk_inputs(session, "VOBL")
    assert weather.visibility_m == 8000


async def test_forecast_rows_are_never_scored(session):
    """`is_forecast` is filtered in the query, so a TAF cannot reach the risk index."""
    await seed_demo_dataset(session)
    await session.commit()

    weather, _, _ = await load_delay_risk_inputs(session, "VOBL", as_of=BENGALURU_STORM.injected_at)
    assert not weather.source_ref.startswith("taf:")


async def test_connection_from_the_seeded_database(session):
    await seed_demo_dataset(session)
    await session.commit()

    itineraries, flights = await load_connection_inputs(session, AFFECTED_FLIGHT_IDS)
    constraints = await load_business_constraints(session)

    result = await ConnectionService().execute(
        itineraries=itineraries,
        flights=flights,
        affected_flight_ids=AFFECTED_FLIGHT_IDS,
        business_constraints=constraints,
    )
    assert result.status is ActionStatus.success
    assert result.payload["at_risk_count"] == 22
    assert result.payload["minimum_connection_minutes"] == 45
    assert result.payload["connecting_itineraries_examined"] == 38


async def test_crew_impact_from_the_seeded_database(session):
    await seed_demo_dataset(session)
    await session.commit()

    affected, pairings, flights = await load_crew_impact_inputs(session, AFFECTED_FLIGHT_IDS)
    assert len(pairings) == 9

    result = await CrewImpactService().execute(
        affected_flights=affected, pairings=pairings, flights=flights
    )
    assert result.status is ActionStatus.success
    assert result.payload["pairings_at_risk"] == 9
    assert result.payload["mechanism_counts"] == {
        "operating": 6,
        "onward_duty": 1,
        "second_pairing": 1,
        "positioning": 1,
    }
    assert "7 + 2 = 9" in result.payload["identity"]


async def test_the_whole_chain_agrees_with_the_committed_fixture(session):
    """seeded DB -> Delay Risk -> Connection -> Crew Impact, against the numbers the UI
    renders from `fixtures/api/incident_group_detail.json`."""
    import json

    from app.config import REPO_ROOT

    await seed_demo_dataset(session)
    await session.commit()

    fixture = json.loads(
        (REPO_ROOT / "fixtures" / "api" / "incident_group_detail.json").read_text(encoding="utf-8")
    )
    rollups = fixture["rollups"]

    weather, runways, ruleset = await load_delay_risk_inputs(
        session, "VOBL", as_of=BENGALURU_STORM.injected_at
    )
    risk = await DelayRiskService().execute(
        weather=weather, runways=runways, ruleset=ruleset, event_threshold=75
    )

    itineraries, flights = await load_connection_inputs(session, AFFECTED_FLIGHT_IDS)
    connection = await ConnectionService().execute(
        itineraries=itineraries, flights=flights, affected_flight_ids=AFFECTED_FLIGHT_IDS
    )

    affected, pairings, crew_flights = await load_crew_impact_inputs(session, AFFECTED_FLIGHT_IDS)
    crew = await CrewImpactService().execute(
        affected_flights=affected, pairings=pairings, flights=crew_flights
    )

    assert risk.payload["risk_level"] == "severe"
    assert connection.payload["at_risk_count"] == rollups["connections_at_risk"]
    assert crew.payload["pairings_at_risk"] == rollups["crew_pairings_affected"]
    assert len(AFFECTED_FLIGHT_IDS) == rollups["flights_affected"]


# --------------------------------------------------- SQLite must be as strict as Postgres


async def test_sqlite_enforces_foreign_keys_in_these_tests(tmp_path):
    """Guards the guard.

    With foreign keys off — SQLite's default — `reset_demo_dataset` deleting incidents while
    `decision_log` still referenced them passed here and raised
    `ForeignKeyViolationError` on Postgres. `make reset` would have failed on the demo machine
    and nowhere else. If this assertion is ever removed, that whole class of bug becomes
    invisible again.
    """
    from tests.contract.sqlite_support import foreign_keys_are_enforced

    engine = create_sqlite_engine(tmp_path / "pragma.db")
    try:
        assert await foreign_keys_are_enforced(engine) is True
    finally:
        await engine.dispose()


async def test_reset_leaves_no_orphaned_workflow_rows(session):
    """A reset must be safe to run after a demo, not only before one."""
    from sqlalchemy import func, select

    from app.models.workflow import DecisionLog, Incident, Prediction

    await seed_demo_dataset(session)
    await session.commit()

    # Stand in for a run: a prediction, an incident referencing it, and an audit entry.
    prediction = Prediction(
        flight_id=BENGALURU_STORM.affected_flights[0].flight_id,
        airport_icao="VOBL",
        predicted_at=BENGALURU_STORM.injected_at,
        risk_index=80,
        risk_level="severe",
        rule_version="delay-risk-v1",
        factors=[],
        evidence_refs=[],
    )
    session.add(prediction)
    await session.flush()

    incident = Incident(
        reference="INC-2026-0820-VOBL-99",
        flight_id=BENGALURU_STORM.affected_flights[0].flight_id,
        prediction_id=prediction.id,
        trigger_type="weather",
        severity="high",
        state="detected",
        demo_dataset_id=DEMO_DATASET_ID,
    )
    session.add(incident)
    await session.flush()
    session.add(
        DecisionLog(
            incident_id=incident.id,
            stage="detect",
            actor="orchestrator",
            event_type="INCIDENT_OPENED",
            summary="stand-in for a real run",
        )
    )
    await session.commit()

    await reset_demo_dataset(session)
    await session.commit()

    for model in (DecisionLog, Incident, Prediction):
        remaining = (await session.execute(select(func.count()).select_from(model))).scalar_one()
        assert remaining == 0, f"{model.__tablename__} still has {remaining} rows"
