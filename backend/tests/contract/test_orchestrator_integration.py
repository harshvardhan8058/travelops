"""The real orchestrator dispatching the real services against the seeded dataset.

`real incident → real calculations → real evidence → real orchestrator execution`.

Everything goes through Stream A's `Orchestrator` and `dispatch`, not through the services
directly — the point is to prove the wiring. The arithmetic itself is covered in
`tests/unit/services/`.

## No gate stub

Stream A's #22 fixed the signature drift with Stream B, so the REAL Decision Assurance Gate
runs here against the real `config/assurance.v1.yaml`. Nothing is faked: Stream B's six checks
and fail-closed aggregation decide, exactly as production will.

Runs on SQLite so CI holds without Postgres; the same path is verified against real Postgres
locally.
"""

from __future__ import annotations

import pytest
from data.generators.cascade_spec import BENGALURU_STORM
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import LLMMode, PolicyMode, Settings
from app.db.assessment import record_delay_risk_prediction
from app.db.base import Base
from app.db.seed import DEMO_DATASET_ID, INCIDENT_GROUP_REFERENCE, seed_demo_dataset
from app.models.enums import (
    ActionStatus,
    ActionType,
    AssuranceDecision,
    IncidentState,
    RiskLevel,
    TaskState,
)
from app.models.reference import WeatherObservation
from app.models.workflow import (
    Action,
    AssuranceEvaluation,
    Incident,
    IncidentGroup,
    Prediction,
)
from app.models.workflow import PlanTask as PlanTaskRow
from app.orchestrator import dispatch
from app.orchestrator.engine import Orchestrator
from app.services.registry import (
    IMPLEMENTED_ACTIONS,
    bind_session,
    register_all,
    unregister_all,
)
from tests.contract.sqlite_support import create_sqlite_engine

SCENARIO_CLOCK = BENGALURU_STORM.injected_at
AFFECTED = [flight.flight_id for flight in BENGALURU_STORM.affected_flights]
PRIMARY_FLIGHT_ID = AFFECTED[0]

EXPECTED_RISK_INDEX = 80
EXPECTED_AT_RISK_CONNECTIONS = 22
EXPECTED_PAIRINGS = 9


def _as_utc(value):
    from datetime import UTC

    return value if value.tzinfo else value.replace(tzinfo=UTC)


@pytest.fixture
def settings() -> Settings:
    """`LLM_MODE=off`: the deterministic path must complete without a model."""
    return Settings(
        app_env="test",
        llm_mode=LLMMode.off,
        policy_mode=PolicyMode.charter,
        database_url="sqlite+aiosqlite://",
        demo_dataset_id=DEMO_DATASET_ID,
        delay_risk_event_threshold=75,
        # No allowlist: every synthetic recipient must record as simulated.
        demo_recipient_allowlist="",
    )


@pytest.fixture
def gate(settings):
    """The REAL Decision Assurance Gate, reading the real `config/assurance.v1.yaml`.

    Stream A's #22 fixed the signature drift with Stream B, so no stub is needed and none is
    used: this exercises Stream B's six checks and fail-closed aggregation exactly as
    production will. Returned as the loaded config so a test can assert it was actually
    available — `assurance_adapter.evaluate` refuses outright when it is not, and a silently
    unavailable gate would make every assertion below vacuous.
    """
    from app.orchestrator.assurance_adapter import load_config

    config, digest = load_config()
    assert config is not None, (
        "config/assurance.v1.yaml did not load; every incident would block and this whole "
        "file would pass for the wrong reason"
    )
    assert digest
    return config


@pytest.fixture
async def session(tmp_path):
    engine = create_sqlite_engine(tmp_path / "orchestrated.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as active:
        await seed_demo_dataset(active)
        await active.commit()
        yield active
    await engine.dispose()


@pytest.fixture
def registered():
    """Register exactly as production would, and clean up after."""
    actions = register_all()
    try:
        yield actions
    finally:
        unregister_all()


@pytest.fixture
def orchestrator(session, settings) -> Orchestrator:
    return Orchestrator(session, settings=settings, now=lambda: SCENARIO_CLOCK)


async def _open_cascade(orchestrator: Orchestrator, session) -> list:
    """Open one incident per affected flight, all in the storm group.

    This is the shape the scenario describes: one weather event owning eight flight
    incidents. It is what makes the group-level counts — 22 connections, 9 rotations — the
    right answer rather than a single flight's much smaller numbers.
    """
    group = (
        (
            await session.execute(
                select(IncidentGroup).where(IncidentGroup.reference == INCIDENT_GROUP_REFERENCE)
            )
        )
        .scalars()
        .one()
    )

    contexts = []
    for flight_id in AFFECTED:
        record = await record_delay_risk_prediction(
            session,
            airport_icao=BENGALURU_STORM.root_airport_icao,
            flight_id=flight_id,
            as_of=SCENARIO_CLOCK,
            event_threshold=75,
        )
        contexts.append(
            await orchestrator.open_incident(
                flight_id,
                "weather",
                group_id=group.id,
                prediction_id=record.prediction_id,
                evidence_refs=list(record.result.evidence_refs),
            )
        )
    return contexts


async def _action_by_reason(session, fragment: str) -> Action | None:
    rows = (await session.execute(select(Action))).scalars().all()
    return next((row for row in rows if fragment in row.reason), None)


# ---------------------------------------------------- the cross-stream defect, documented


# ------------------------------------------------------------------------ registration


def test_register_all_binds_only_the_implemented_services(registered):
    assert set(registered) == set(IMPLEMENTED_ACTIONS)
    for action in registered:
        assert dispatch.is_implemented(action)


def test_unimplemented_actions_still_refuse(registered):
    """A visible gap beats a green run that did nothing. The other six must keep refusing."""
    for action in (
        ActionType.find_hotel_options,
        ActionType.reserve_hotel_block,
        ActionType.arrange_ground_transport,
        ActionType.rebook_passengers,
        ActionType.reassign_gate,
        ActionType.evaluate_entitlements,
        ActionType.record_outcome,
    ):
        assert not dispatch.is_implemented(action)


async def test_an_unimplemented_action_returns_the_refusal_contract(registered):
    result = await dispatch.dispatch(ActionType.find_hotel_options, target_refs=[], inputs={})
    assert result.status is ActionStatus.needs_human
    assert result.payload["reason_code"] == dispatch.SERVICE_NOT_IMPLEMENTED
    assert result.payload["owning_service"] == "hotel"


def test_action_owners_remains_authoritative(registered):
    """The registry must agree with dispatch, and dispatch wins."""
    for action, service_name in IMPLEMENTED_ACTIONS.items():
        assert dispatch.ACTION_OWNERS[action] == service_name


def test_registration_fails_loudly_if_owners_disagree():
    """A registry bound to the wrong service would name one owner in the audit trail while
    running another's logic. Registration must refuse rather than ship that."""
    from app.services.registry import _assert_owners_agree

    with pytest.raises(RuntimeError, match="ACTION_OWNERS is authoritative"):
        _assert_owners_agree({ActionType.check_connections: "hotel"})


def test_registry_does_not_import_the_orchestrator_at_module_scope():
    """The dependency direction is orchestrator -> services. Inverting it at import time
    creates a cycle through `app.services.base` whose symptom is a confusing AttributeError
    at startup."""
    import ast
    from pathlib import Path

    import app.services.registry as module

    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Import | ast.ImportFrom):
            assert "orchestrator" not in ast.dump(node)


def test_unregister_leaves_other_registrations_alone(registered):
    async def other(**_kwargs):
        raise AssertionError("never called")

    dispatch.register(ActionType.record_outcome, other)
    try:
        unregister_all()
        assert dispatch.is_implemented(ActionType.record_outcome)
        assert not dispatch.is_implemented(ActionType.check_connections)
    finally:
        dispatch.SERVICE_REGISTRY.pop(ActionType.record_outcome, None)


# --------------------------------------------------------------------------- delay risk


async def test_delay_risk_prediction_is_recorded(session):
    record = await record_delay_risk_prediction(
        session,
        airport_icao="VOBL",
        flight_id=PRIMARY_FLIGHT_ID,
        as_of=SCENARIO_CLOCK,
        event_threshold=75,
    )
    assert record.risk_index == EXPECTED_RISK_INDEX
    assert record.risk_level == RiskLevel.severe.value
    assert record.event_recommended is True
    assert record.prediction_id is not None

    row = await session.get(Prediction, record.prediction_id)
    assert row is not None
    assert row.risk_index == EXPECTED_RISK_INDEX
    assert row.rule_version == "delay-risk-v1"
    assert row.factors
    assert any(ref.startswith("ruleset_hash:") for ref in row.evidence_refs)


async def test_prediction_uses_the_scenario_clock_not_the_latest_row(session):
    """Without `as_of` this scores the clear-weather archive and opens no incident at all."""
    from app.db.scenario_queries import load_delay_risk_inputs

    scenario, _, _ = await load_delay_risk_inputs(session, "VOBL", as_of=SCENARIO_CLOCK)
    latest, _, _ = await load_delay_risk_inputs(session, "VOBL")
    assert scenario.visibility_m == 800
    assert latest.visibility_m == 8000


async def test_prediction_stores_an_index_not_a_probability(session):
    record = await record_delay_risk_prediction(
        session, airport_icao="VOBL", flight_id=PRIMARY_FLIGHT_ID, as_of=SCENARIO_CLOCK
    )
    row = await session.get(Prediction, record.prediction_id)
    assert row is not None
    assert 0 <= row.risk_index <= 100
    assert "probability" not in str(row.factors)


async def test_incident_carries_its_prediction(session, orchestrator):
    record = await record_delay_risk_prediction(
        session,
        airport_icao="VOBL",
        flight_id=PRIMARY_FLIGHT_ID,
        as_of=SCENARIO_CLOCK,
        event_threshold=75,
    )
    ctx = await orchestrator.open_incident(
        PRIMARY_FLIGHT_ID, "weather", prediction_id=record.prediction_id
    )
    incident = await session.get(Incident, ctx.incident_id)
    assert incident is not None
    assert incident.prediction_id == record.prediction_id


async def test_a_refused_assessment_records_no_prediction_row(session, monkeypatch):
    """An unscored incident must not acquire a record implying it was assessed."""
    from app.db import assessment as module
    from app.services.delay_risk import DEFAULT_RULESET, WeatherInput

    async def unusable(_session, _airport, *, as_of):
        return (
            WeatherInput(airport_icao="VOBL", wind_speed_kt=None, visibility_m=None),
            [],
            DEFAULT_RULESET,
        )

    monkeypatch.setattr(module, "load_delay_risk_inputs", unusable)

    record = await record_delay_risk_prediction(
        session, airport_icao="VOBL", flight_id=PRIMARY_FLIGHT_ID, as_of=SCENARIO_CLOCK
    )
    assert record.prediction_id is None
    assert record.result.status is ActionStatus.needs_human
    assert (await session.execute(select(Prediction))).scalars().all() == []


# ------------------------------------------------------- dispatch through the engine


async def test_the_run_is_held_by_the_real_gate_on_source_freshness(
    session, orchestrator, registered, gate
):
    """The honest end-to-end state on `main` today, and exactly why.

    `Orchestrator._source_timestamps` selects the newest `weather_observation` for the origin
    airport with no `is_forecast` filter and no bound on the assessment clock. The seeded
    dataset holds the real archived AWC observations on their own true timestamps
    (2026-08-21) alongside the injected scenario observation (2026-08-20), so the query hands
    the gate a **future-dated** timestamp and Stream B's `sources_fresh` FAILs it — correctly,
    because a broken feed must not read as maximally fresh.

    The result is `needs_human` on a low-risk assessment task, so the run stops at
    `awaiting_approval` and no service is dispatched. Neither file is Stream C's:
    `app/orchestrator/engine.py` needs `is_forecast=False` and `observed_at <= now`, for which
    `app.db.scenario_queries.latest_actual_observation_at` is provided.

    Asserted rather than skipped, so the blocker cannot be quietly forgotten.
    """
    await _open_cascade(orchestrator, session)
    ctx = await orchestrator.load_context(1)
    with bind_session(session):
        await orchestrator.run(ctx)

    evaluations = (await session.execute(select(AssuranceEvaluation))).scalars().all()
    assert evaluations, "the gate was never consulted"
    assert str(evaluations[0].decision) == AssuranceDecision.needs_human.value
    assert evaluations[0].blocking_reasons == ["sources_fresh"]
    assert ctx.state is IncidentState.awaiting_approval

    # Nothing ran, and nothing claimed to.
    assert (await session.execute(select(Action))).scalars().all() == []


async def test_the_gate_authorises_when_the_source_timestamp_is_bounded(
    session, orchestrator, registered, gate
):
    """With a correctly bounded source timestamp the real gate authorises and dispatch runs.

    Same gate, same config, same six checks. The only change is that the source timestamp
    comes from `latest_actual_observation_at`, which filters forecasts and bounds by the
    assessment clock. That isolates the blocker to one query and shows the rest of the chain
    is ready.
    """
    from app.db.scenario_queries import latest_actual_observation_at

    bounded = await latest_actual_observation_at(session, "VOBL", as_of=SCENARIO_CLOCK)
    assert bounded is not None

    original = orchestrator._source_timestamps

    async def bounded_sources(ctx):
        await original(ctx)
        return {"metar:VOBL": bounded}

    orchestrator._source_timestamps = bounded_sources

    await _open_cascade(orchestrator, session)
    ctx = await orchestrator.load_context(1)
    with bind_session(session):
        await orchestrator.run(ctx)

    evaluation = (await session.execute(select(AssuranceEvaluation))).scalars().first()
    assert evaluation is not None
    assert str(evaluation.decision) in {
        AssuranceDecision.execute.value,
        AssuranceDecision.execute_flagged.value,
    }
    assert evaluation.config_version
    assert evaluation.config_hash

    action = await _action_by_reason(session, "itineraries no longer feasible")
    assert action is not None, "check_connections was authorised but never dispatched"
    assert action.status == ActionStatus.success
    assert action.payload["at_risk_count"] == EXPECTED_AT_RISK_CONNECTIONS
    assert action.payload["minimum_connection_minutes"] == 45
    assert action.provenance_kind == "synthetic"
    assert action.assurance_id == evaluation.id

    # Evidence a controller can follow all the way to a passenger.
    sample = action.payload["at_risk"][0]
    assert sample["pnr"]
    assert sample["passenger_reference"].startswith("PAX-")
    assert sample["inbound_segment_id"] != sample["onward_segment_id"]
    assert sample["shortfall_minutes"] < 0

    # The task state records the real outcome.
    rows = (await session.execute(select(PlanTaskRow))).scalars().all()
    succeeded = {r.action_type for r in rows if TaskState(r.state) is TaskState.succeeded}
    assert ActionType.check_connections.value in succeeded


async def test_the_next_blocker_after_freshness_is_the_unbuilt_hotel_service(
    session, orchestrator, registered, gate
):
    """Once the assessment is authorised the run reaches `find_hotel_options`, which is
    deliberately not built, and dispatch refuses rather than reporting success."""
    from app.db.scenario_queries import latest_actual_observation_at

    bounded = await latest_actual_observation_at(session, "VOBL", as_of=SCENARIO_CLOCK)

    async def bounded_sources(_ctx):
        return {"metar:VOBL": bounded}

    orchestrator._source_timestamps = bounded_sources

    await _open_cascade(orchestrator, session)
    ctx = await orchestrator.load_context(1)
    with bind_session(session):
        await orchestrator.run(ctx)

    refused = await _action_by_reason(session, dispatch.SERVICE_NOT_IMPLEMENTED)
    assert refused is not None
    assert refused.status == ActionStatus.needs_human
    assert refused.payload["owning_service"] == "hotel"
    assert refused.provenance_kind == "unavailable"


async def test_the_bounded_helper_excludes_forecasts_and_future_rows(session):
    """The two ways this schema can hand a freshness check something it must reject."""
    from app.db.scenario_queries import latest_actual_observation_at

    bounded = await latest_actual_observation_at(session, "VOBL", as_of=SCENARIO_CLOCK)
    assert bounded is not None
    assert bounded <= SCENARIO_CLOCK

    unbounded = (
        await session.execute(
            select(WeatherObservation.observed_at)
            .where(WeatherObservation.airport_icao == "VOBL")
            .order_by(WeatherObservation.observed_at.desc())
            .limit(1)
        )
    ).scalar_one()
    assert _as_utc(unbounded) > SCENARIO_CLOCK, (
        "the seeded archive is no longer future-dated relative to the scenario; this test's "
        "premise has changed and the freshness blocker may be gone"
    )


async def test_crew_impact_runs_through_the_dispatch_boundary(session, orchestrator, registered):
    """Real registration, real dispatch, real service, group-scoped: nine rotations."""
    await _open_cascade(orchestrator, session)
    incident = await session.get(Incident, 1)
    assert incident is not None

    with bind_session(session):
        result = await dispatch.dispatch(
            ActionType.assess_crew_impact,
            target_refs=[f"incident:{incident.reference}", f"flight:{incident.flight_id}"],
            inputs={},
        )

    assert result.status is ActionStatus.success
    assert result.payload["pairings_at_risk"] == EXPECTED_PAIRINGS
    assert result.payload["mechanism_counts"] == {
        "operating": 6,
        "onward_duty": 1,
        "second_pairing": 1,
        "positioning": 1,
    }
    assert "7 + 2 = 9" in result.payload["identity"]
    assert any(ref.startswith("pairing:") for ref in result.evidence_refs)


async def test_connections_run_through_the_dispatch_boundary(session, orchestrator, registered):
    await _open_cascade(orchestrator, session)
    incident = await session.get(Incident, 1)
    assert incident is not None

    with bind_session(session):
        result = await dispatch.dispatch(
            ActionType.check_connections,
            target_refs=[f"incident:{incident.reference}", f"flight:{incident.flight_id}"],
            inputs={},
        )

    assert result.status is ActionStatus.success
    assert result.payload["at_risk_count"] == EXPECTED_AT_RISK_CONNECTIONS
    assert result.payload["at_risk_by_flight"] == {
        "6E 2134": 8,
        "6E 811": 5,
        "AI 503": 3,
        "6E 455": 2,
        "UK 812": 2,
        "UK 705": 2,
    }


# ------------------------------------------------------------- the high-risk boundary


async def test_notification_dispatch_records_only_simulated_deliveries(
    session, orchestrator, registered
):
    """604 synthetic recipients through the real dispatch boundary: zero real sends."""
    await _open_cascade(orchestrator, session)
    incident = await session.get(Incident, 1)
    assert incident is not None

    with bind_session(session):
        result = await dispatch.dispatch(
            ActionType.notify_passengers,
            target_refs=[f"incident:{incident.reference}", f"flight:{incident.flight_id}"],
            inputs={},
        )

    assert result.payload["real_count"] == 0
    assert result.payload["simulated_count"] == 604
    assert result.payload["not_rendered"] == []
    assert "0 message(s) were actually delivered" in result.payload["honesty_note"]


# --------------------------------------------------------------------------- scoping


async def test_scope_is_the_incident_group_not_a_single_flight(session, orchestrator):
    """One weather event owns eight flight incidents. Scoping a cascade assessment to one
    flight would report 2 rotations instead of 9."""
    from app.services.registry import resolve_scope

    await _open_cascade(orchestrator, session)
    incident = await session.get(Incident, 1)
    assert incident is not None

    scope = await resolve_scope(
        session,
        target_refs=[f"incident:{incident.reference}", f"flight:{incident.flight_id}"],
        inputs={},
    )
    assert scope == set(AFFECTED)


async def test_single_flight_incident_scopes_to_that_flight(session, orchestrator):
    from app.services.registry import resolve_scope

    ctx = await orchestrator.open_incident(PRIMARY_FLIGHT_ID, "weather")
    incident = await session.get(Incident, ctx.incident_id)
    assert incident is not None

    scope = await resolve_scope(
        session,
        target_refs=[f"incident:{incident.reference}", f"flight:{PRIMARY_FLIGHT_ID}"],
        inputs={},
    )
    assert scope == {PRIMARY_FLIGHT_ID}


async def test_a_single_flight_scope_reports_fewer_rotations(session, orchestrator):
    """Stated explicitly so nobody reads 9 as a property of one flight: 6E 2134 alone
    accounts for two rotations, and the nine is a property of the group."""
    from app.services.registry import crew_impact_adapter

    result = await crew_impact_adapter(
        target_refs=[f"flight:{PRIMARY_FLIGHT_ID}"],
        inputs={"affected_flight_ids": [PRIMARY_FLIGHT_ID]},
        session=session,
    )
    assert result.payload["pairings_at_risk"] == 2


async def test_explicit_inputs_win_over_the_group(session, orchestrator):
    from app.services.registry import resolve_scope

    await _open_cascade(orchestrator, session)
    scope = await resolve_scope(
        session,
        target_refs=["incident:INC-2026-0820-VOBL-01"],
        inputs={"affected_flight_ids": [3]},
    )
    assert scope == {3}


async def test_unresolvable_scope_refuses_rather_than_reporting_nothing(session):
    """ "0 connections at risk" because the scope was empty would read as good news."""
    from app.services.registry import connection_adapter

    result = await connection_adapter(
        target_refs=["incident:INC-does-not-exist"], inputs={}, session=session
    )
    assert result.status is ActionStatus.needs_human
    assert result.payload["reason_code"] == "SCOPE_UNRESOLVED"
    assert result.provenance_kind == "unavailable"


# ------------------------------------------------------------------- prepare vs notify


async def test_prepare_notifications_cannot_deliver(session):
    """A preparation step must not be able to send, however the process is configured."""
    from app.services.registry import prepare_notifications_adapter

    result = await prepare_notifications_adapter(
        target_refs=[f"flight:{PRIMARY_FLIGHT_ID}"],
        inputs={"affected_flight_ids": [PRIMARY_FLIGHT_ID]},
        session=session,
    )
    assert result.payload["real_count"] == 0
    assert result.payload["simulated_count"] == 174


async def test_notifications_render_every_required_fact(session):
    """A missing fact would refuse the recipient. None may be missing from the query."""
    from app.services.registry import prepare_notifications_adapter

    result = await prepare_notifications_adapter(
        target_refs=[f"flight:{PRIMARY_FLIGHT_ID}"],
        inputs={"affected_flight_ids": [PRIMARY_FLIGHT_ID]},
        session=session,
    )
    assert result.payload["not_rendered"] == []
    assert result.status is ActionStatus.success


# ------------------------------------------------------------------------ determinism


async def test_the_same_incident_dispatches_the_same_numbers_twice(session, orchestrator):
    from app.services.registry import connection_adapter, crew_impact_adapter

    await _open_cascade(orchestrator, session)
    incident = await session.get(Incident, 1)
    assert incident is not None
    refs = [f"incident:{incident.reference}", f"flight:{incident.flight_id}"]

    first = await connection_adapter(target_refs=refs, inputs={}, session=session)
    second = await connection_adapter(target_refs=refs, inputs={}, session=session)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")

    crew_first = await crew_impact_adapter(target_refs=refs, inputs={}, session=session)
    crew_second = await crew_impact_adapter(target_refs=refs, inputs={}, session=session)
    assert crew_first.model_dump(mode="json") == crew_second.model_dump(mode="json")


async def test_seed_remains_deterministic_after_a_full_run(session, orchestrator, registered, gate):
    """The workflow appends decision records; it must not mutate the seeded dataset."""
    from app.db.seed import dataset_counts

    before = await dataset_counts(session)
    await _open_cascade(orchestrator, session)
    ctx = await orchestrator.load_context(1)
    with bind_session(session):
        await orchestrator.run(ctx)
    assert await dataset_counts(session) == before


async def test_reset_after_a_run_removes_the_dataset_and_its_incidents(
    session, orchestrator, registered, gate
):
    from app.db.seed import dataset_counts, reset_demo_dataset

    await _open_cascade(orchestrator, session)
    ctx = await orchestrator.load_context(1)
    with bind_session(session):
        await orchestrator.run(ctx)

    await reset_demo_dataset(session)
    await session.commit()

    assert all(value == 0 for value in (await dataset_counts(session)).values())
    assert (await session.execute(select(Incident))).scalars().all() == []
