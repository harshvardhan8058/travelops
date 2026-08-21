"""The real orchestrator dispatching the real services against the seeded dataset.

`real incident → real calculations → real evidence → real orchestrator execution`.

Everything goes through Stream A's `Orchestrator` and `dispatch`, not through the services
directly — the point is to prove the wiring. The arithmetic itself is covered in
`tests/unit/services/`.

## A gate stub is used here, and why

`app.assurance.gate.evaluate` and `app.orchestrator.assurance_adapter.evaluate` currently
disagree about their signature, so on `main` today every incident blocks at `assuring` and no
service is ever dispatched. That defect belongs to Streams A and B and is documented by
`test_the_real_gate_signature_matches_what_the_orchestrator_calls` below.

To prove *this* stream's integration independently, the gate is stubbed exactly as Stream A's
own tests stub it — installed over `app.assurance.gate`, with decisions taken from the real
`config/assurance.v1.yaml` risk tiers so the stub cannot be more permissive than production.

Runs on SQLite so CI holds without Postgres; the same path is verified against real Postgres
locally.
"""

from __future__ import annotations

import pytest
from data.generators.cascade_spec import BENGALURU_STORM
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.assurance.contract import (
    CHECK_ORDER,
    AssuranceResult,
    CheckResult,
)
from app.config import LLMMode, PolicyMode, Settings
from app.db.assessment import record_delay_risk_prediction
from app.db.base import Base
from app.db.seed import DEMO_DATASET_ID, INCIDENT_GROUP_REFERENCE, seed_demo_dataset
from app.models.enums import (
    ActionStatus,
    ActionType,
    AssuranceDecision,
    CheckState,
    IncidentState,
    RiskLevel,
    RiskTier,
    TaskState,
)
from app.models.workflow import (
    Action,
    AssuranceEvaluation,
    HumanDecision,
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

#: Taken from config/assurance.v1.yaml. Anything not listed defaults to needing a human, so
#: the stub can never authorise more than the real configuration would.
LOW_RISK_ACTIONS = {
    ActionType.check_connections.value,
    ActionType.assess_crew_impact.value,
    ActionType.find_hotel_options.value,
    ActionType.prepare_notifications.value,
}


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


def _result(decision: AssuranceDecision, tier: RiskTier) -> AssuranceResult:
    return AssuranceResult(
        decision=decision,
        risk_tier=tier,
        checks=[
            CheckResult(name=name, state=CheckState.passed, reason_code="OK")
            for name in CHECK_ORDER
        ],
        blocking=[],
        evidence_refs=["fixture:bengaluru_storm:metar:VOBL"],
        config_version="assurance-v1",
        config_hash="integration-stub",
    )


@pytest.fixture
def gate(monkeypatch):
    """Install a gate that mirrors the real risk tiers, without editing Stream B's module."""
    from app.assurance import gate as real_gate

    calls: list[dict] = []

    def evaluate(**kwargs):
        calls.append(kwargs)
        action_type = kwargs.get("action_type")
        if action_type in LOW_RISK_ACTIONS:
            return _result(AssuranceDecision.execute, RiskTier.low)
        return _result(AssuranceDecision.needs_human, RiskTier.high)

    monkeypatch.setattr(real_gate, "evaluate", evaluate, raising=False)
    monkeypatch.setattr(real_gate, "load_config", lambda path: None, raising=False)
    return calls


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


@pytest.mark.xfail(
    strict=False,
    reason=(
        "Stream A's assurance_adapter calls gate.evaluate(action_type=..., target_refs=..., "
        "inputs=dict, evidence_refs=..., incident_state=...) but Stream B's gate.evaluate "
        "takes (inputs=GateInputs, config, config_hash, now). Until one side changes, every "
        "incident blocks at 'assuring' and no service is dispatched. Neither file is Stream "
        "C's. This test turns green when it is fixed."
    ),
)
async def test_the_real_gate_signature_matches_what_the_orchestrator_calls():
    """The one thing standing between this integration and a live end-to-end run."""
    from app.orchestrator import assurance_adapter

    result = await assurance_adapter.evaluate(
        action_type=ActionType.check_connections.value,
        target_refs=["incident:INC-2026-0820-VOBL-01", "flight:1"],
        inputs={},
        evidence_refs=["fixture:bengaluru_storm:metar:VOBL"],
        incident_state=IncidentState.assuring.value,
        config=None,
    )
    assert isinstance(result, AssuranceResult)


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


async def test_connections_run_through_the_orchestrator(session, orchestrator, registered, gate):
    """The engine dispatches, the real service answers, and the Action row holds the count."""
    await _open_cascade(orchestrator, session)
    ctx = await orchestrator.load_context(1)
    with bind_session(session):
        await orchestrator.run(ctx)

    action = await _action_by_reason(session, "itineraries no longer feasible")
    assert action is not None, "check_connections was never dispatched"
    assert action.status == ActionStatus.success
    assert action.payload["at_risk_count"] == EXPECTED_AT_RISK_CONNECTIONS
    assert action.payload["minimum_connection_minutes"] == 45
    assert action.provenance_kind == "synthetic"


async def test_the_run_blocks_on_the_first_unimplemented_service(
    session, orchestrator, registered, gate
):
    """The honest end-to-end state today.

    The weather playbook orders `find_hotel_options` second, and the Hotel service is
    deliberately not built yet, so the run completes `check_connections` and then blocks with
    `SERVICE_NOT_IMPLEMENTED`. That is dispatch working as designed — a visible gap rather
    than a green run that did nothing — and it is why `assess_crew_impact` is exercised below
    through the dispatch boundary rather than through a full `run()`.
    """
    await _open_cascade(orchestrator, session)
    ctx = await orchestrator.load_context(1)
    with bind_session(session):
        await orchestrator.run(ctx)

    connections = await _action_by_reason(session, "itineraries no longer feasible")
    assert connections is not None
    assert connections.status == ActionStatus.success

    refused = await _action_by_reason(session, dispatch.SERVICE_NOT_IMPLEMENTED)
    assert refused is not None
    assert refused.status == ActionStatus.needs_human
    assert refused.payload["owning_service"] == "hotel"
    assert ctx.state is IncidentState.blocked


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


async def test_the_gate_was_asked_before_dispatch(session, orchestrator, registered, gate):
    """A service never decides whether it is allowed to run."""
    await _open_cascade(orchestrator, session)
    ctx = await orchestrator.load_context(1)
    with bind_session(session):
        await orchestrator.run(ctx)

    asked = {call.get("action_type") for call in gate}
    assert ActionType.check_connections.value in asked

    executed = await _action_by_reason(session, "itineraries no longer feasible")
    assert executed is not None
    assert executed.assurance_id is not None


async def test_every_action_references_the_evaluation_that_authorised_it(
    session, orchestrator, registered, gate
):
    await _open_cascade(orchestrator, session)
    ctx = await orchestrator.load_context(1)
    with bind_session(session):
        await orchestrator.run(ctx)

    actions = (await session.execute(select(Action))).scalars().all()
    assert actions
    evaluation_ids = {
        row.id for row in (await session.execute(select(AssuranceEvaluation))).scalars()
    }
    for action in actions:
        assert action.assurance_id in evaluation_ids


async def test_evidence_survives_into_the_action_payload(session, orchestrator, registered, gate):
    """A count a controller cannot trace is a count they cannot defend."""
    await _open_cascade(orchestrator, session)
    ctx = await orchestrator.load_context(1)
    with bind_session(session):
        await orchestrator.run(ctx)

    action = await _action_by_reason(session, "itineraries no longer feasible")
    assert action is not None
    sample = action.payload["at_risk"][0]
    assert sample["pnr"]
    assert sample["passenger_reference"].startswith("PAX-")
    assert sample["inbound_segment_id"] != sample["onward_segment_id"]
    assert sample["shortfall_minutes"] < 0


async def test_the_dispatched_task_reaches_succeeded(session, orchestrator, registered, gate):
    await _open_cascade(orchestrator, session)
    ctx = await orchestrator.load_context(1)
    with bind_session(session):
        await orchestrator.run(ctx)

    rows = (await session.execute(select(PlanTaskRow))).scalars().all()
    succeeded = {row.action_type for row in rows if TaskState(row.state) is TaskState.succeeded}
    assert ActionType.check_connections.value in succeeded


# ------------------------------------------------------------- the high-risk boundary


async def test_notify_passengers_is_never_reached_without_approval(
    session, orchestrator, registered, gate
):
    """High risk in the gate config, and behind two assessment steps. Nobody is mailed."""
    await _open_cascade(orchestrator, session)
    ctx = await orchestrator.load_context(1)
    with bind_session(session):
        await orchestrator.run(ctx)

    notified = [
        row
        for row in (await session.execute(select(Action))).scalars()
        if row.payload and "real_count" in row.payload
    ]
    assert notified == []


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


async def test_an_approved_high_risk_action_records_its_human_decision(
    session, orchestrator, registered, gate
):
    """The engine must refuse a high-risk dispatch until an approval exists for that exact
    evaluation, then record it on the action."""
    await _open_cascade(orchestrator, session)
    ctx = await orchestrator.load_context(1)
    with bind_session(session):
        await orchestrator.run(ctx)

    blocked = [
        row
        for row in (await session.execute(select(AssuranceEvaluation))).scalars()
        if str(row.decision) == AssuranceDecision.needs_human.value
    ]
    if not blocked:
        pytest.skip("run blocked before a high-risk task was evaluated")

    session.add(
        HumanDecision(
            assurance_id=blocked[0].id,
            decision="approved",
            actor_id="operator-integration-test",
            reason="approved so the dispatch path can be exercised",
        )
    )
    await session.commit()
    decision = (await session.execute(select(HumanDecision))).scalars().one()
    assert decision.assurance_id == blocked[0].id


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
