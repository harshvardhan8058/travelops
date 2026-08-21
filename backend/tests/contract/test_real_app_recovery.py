"""The demo path, through the real application, against real Postgres.

    POST /run  ->  awaiting_approval  ->  approve  ->  POST /run  ->  resolved

Nothing here is a stand-in:

* the real FastAPI app, over its real routers and response models;
* the real `Orchestrator`, the real Decision Assurance Gate reading the real
  `config/assurance.v1.yaml`, and the real `dispatch.SERVICE_REGISTRY` populated by Stream A's
  `register_stage2_services()` — the one registration path, not a second one;
* the real seeded dataset from `app.db.seed`, on real Postgres with real migrations applied;
* the real deterministic services, returning their own payloads.

No service output is faked and no behaviour is patched. The only thing overridden is the
app's `get_session` dependency, so it talks to the test database rather than the one in the
environment — the same technique Stream A's own e2e harness uses.

Skips unless `TRAVELOPS_TEST_DATABASE_URL` is set. See `postgres_support`.

Owner: Stream C.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from data.generators.cascade_spec import BENGALURU_STORM
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.scenario_queries import affected_pairings_recursive, cascade_rollup
from app.db.seed import (
    DEMO_DATASET_ID,
    INCIDENT_GROUP_REFERENCE,
    reset_demo_dataset,
    seed_demo_dataset,
)
from app.models.enums import ActionStatus, ActionType, IncidentState
from app.models.workflow import Action, Incident, IncidentGroup, Prediction
from app.orchestrator import dispatch
from app.orchestrator.engine import Orchestrator
from tests.contract.postgres_support import create_postgres_engine, requires_postgres

PREFIX = "/api/v1"

SCENARIO_CLOCK = BENGALURU_STORM.injected_at
AFFECTED_FLIGHT_IDS = [flight.flight_id for flight in BENGALURU_STORM.affected_flights]
PRIMARY_FLIGHT_ID = AFFECTED_FLIGHT_IDS[0]

EXPECTED_RISK_INDEX = 80
EXPECTED_RISK_LEVEL = "severe"
EXPECTED_CONNECTIONS = 22
EXPECTED_PAIRINGS = 9
EXPECTED_PASSENGERS = 604
EXPECTED_HOTELS = 11

pytestmark = requires_postgres


@pytest.fixture
async def engine() -> AsyncIterator:
    db = create_postgres_engine()
    yield db
    await db.dispose()


@pytest.fixture
async def sessionmaker_for(engine):
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@pytest.fixture
async def seeded(sessionmaker_for) -> AsyncIterator[None]:
    """The committed dataset, seeded and torn down around each test.

    Migrations are expected to have been applied already (`alembic upgrade head`); this
    fixture owns rows, not schema, so a test can never mask a missing migration.
    """
    async with sessionmaker_for() as session:
        await _clear_workflow(session)
        await reset_demo_dataset(session)
        await seed_demo_dataset(session)
        await session.commit()
    yield
    async with sessionmaker_for() as session:
        await _clear_workflow(session)
        await reset_demo_dataset(session)
        await session.commit()


async def _clear_workflow(session) -> None:
    """Remove any workflow output left by an earlier run, child-first."""
    from sqlalchemy import delete

    from app.models.workflow import (
        Action as ActionRow,
    )
    from app.models.workflow import (
        AssuranceEvaluation,
        DecisionLog,
        HumanDecision,
        Notification,
        Plan,
        PlanTask,
    )

    await session.execute(delete(Notification))
    await session.execute(delete(ActionRow))
    await session.execute(delete(HumanDecision))
    await session.execute(delete(AssuranceEvaluation))
    await session.execute(delete(PlanTask))
    await session.execute(delete(Plan))
    await session.execute(delete(DecisionLog))
    await session.flush()


@pytest.fixture
def registered() -> AsyncIterator[list[str]]:
    """Stream A's registration path, and only that one.

    `SERVICE_REGISTRY` is process-global, so it is populated explicitly here rather than
    relying on the app lifespan having run first.
    """
    from app.orchestrator.service_registry import register_stage2_services

    dispatch.SERVICE_REGISTRY.clear()
    actions = register_stage2_services()
    yield actions
    dispatch.SERVICE_REGISTRY.clear()


@pytest.fixture
def client(sessionmaker_for, seeded, registered) -> AsyncIterator[TestClient]:
    """The real app, pointed at the test database."""
    from app.db.session import get_session
    from app.main import app

    async def override() -> AsyncIterator:
        async with sessionmaker_for() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = override
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_session, None)


async def _open_incident(sessionmaker_for, flight_id: int) -> str:
    """Open one incident through the real engine, exactly as the CLI's inject does."""
    async with sessionmaker_for() as session:
        group = (
            (
                await session.execute(
                    select(IncidentGroup).where(IncidentGroup.reference == INCIDENT_GROUP_REFERENCE)
                )
            )
            .scalars()
            .one()
        )
        orchestrator = Orchestrator(session)
        ctx = await orchestrator.open_incident(
            flight_id,
            group.root_cause,
            severity=group.severity,
            group_id=group.id,
            demo_dataset_id=DEMO_DATASET_ID,
            evidence_refs=[f"fixture:{DEMO_DATASET_ID}:weather:{group.airport_icao}"],
            opened_at=SCENARIO_CLOCK,
        )
        await session.commit()
        return ctx.incident_reference


def _run(client: TestClient, reference: str) -> dict:
    response = client.post(f"{PREFIX}/incidents/{reference}/run")
    assert response.status_code == 200, response.text
    return response.json()


def _approve(client: TestClient, evaluation_id: int, reason: str) -> None:
    response = client.post(
        f"{PREFIX}/assurance/{evaluation_id}/decision",
        json={"decision": "approved", "reason": reason},
    )
    assert response.status_code == 200, response.text


def _pending(client: TestClient, reference: str) -> list[dict]:
    body = client.get(f"{PREFIX}/incidents/{reference}/assurance").json()
    return [e for e in body["evaluations"] if e["decision"] == "needs_human"]


def _drive_to_terminal(client: TestClient, reference: str, *, max_approvals: int = 6) -> dict:
    """run → approve everything the gate holds → run, until terminal or no progress."""
    state = _run(client, reference)
    for _ in range(max_approvals):
        if state["is_terminal"]:
            return state
        pending = _pending(client, reference)
        if not pending:
            return state
        for evaluation in pending:
            _approve(client, evaluation["id"], "approved by the integration test operator")
        state = _run(client, reference)
    return state


# ------------------------------------------------------------------ the required flow


async def test_run_then_approve_then_run_reaches_resolved(client, sessionmaker_for):
    """POST /run → awaiting_approval → approve → POST /run → resolved."""
    reference = await _open_incident(sessionmaker_for, PRIMARY_FLIGHT_ID)

    first = _run(client, reference)
    assert first["state"] == IncidentState.awaiting_approval.value
    assert first["is_terminal"] is False

    pending = _pending(client, reference)
    assert len(pending) == 1
    assert pending[0]["action_type"] == ActionType.notify_passengers.value

    _approve(client, pending[0]["id"], "confirmed against the ops board")

    second = _run(client, reference)
    assert second["state"] == IncidentState.resolved.value
    assert second["is_terminal"] is True


async def test_the_approval_is_recorded_against_the_action(client, sessionmaker_for):
    """No side effect without authorisation, and a high-risk one names its approver."""
    reference = await _open_incident(sessionmaker_for, PRIMARY_FLIGHT_ID)
    _drive_to_terminal(client, reference)

    async with sessionmaker_for() as session:
        actions = (await session.execute(select(Action))).scalars().all()
        assert actions
        for action in actions:
            assert action.assurance_id is not None

        notified = [a for a in actions if a.payload and "real_count" in a.payload]
        assert notified, "notify_passengers never dispatched"
        for action in notified:
            assert action.human_decision_id is not None


async def test_no_run_is_possible_before_approval(client, sessionmaker_for):
    """Re-running without approving must not advance past the gate."""
    reference = await _open_incident(sessionmaker_for, PRIMARY_FLIGHT_ID)

    first = _run(client, reference)
    second = _run(client, reference)
    assert first["state"] == IncidentState.awaiting_approval.value
    assert second["state"] == IncidentState.awaiting_approval.value

    async with sessionmaker_for() as session:
        notified = [
            a
            for a in (await session.execute(select(Action))).scalars()
            if a.payload and "real_count" in a.payload
        ]
        assert notified == []


# --------------------------------------------------------------------- the real values


async def test_delay_risk_is_eighty_severe_through_the_api(client, sessionmaker_for):
    reference = await _open_incident(sessionmaker_for, PRIMARY_FLIGHT_ID)
    _run(client, reference)

    risk = client.get(f"{PREFIX}/incidents/{reference}").json()["evidence"]["risk"]
    assert risk["risk_index"] == EXPECTED_RISK_INDEX
    assert risk["risk_level"] == EXPECTED_RISK_LEVEL
    assert risk["rule_version"] == "delay-risk-v1"

    factors = {factor["name"] for factor in risk["factors"]}
    assert "visibility_low_visibility_procedures" in factors
    assert "low_visibility_with_low_ceiling" in factors
    # An index from named bands, never a probability.
    assert "probability" not in risk
    assert "confidence" not in risk


async def test_the_prediction_row_is_written_once_per_incident(client, sessionmaker_for):
    reference = await _open_incident(sessionmaker_for, PRIMARY_FLIGHT_ID)
    _run(client, reference)
    _run(client, reference)

    async with sessionmaker_for() as session:
        predictions = (await session.execute(select(Prediction))).scalars().all()
        assert len(predictions) == 1
        assert predictions[0].risk_index == EXPECTED_RISK_INDEX
        incident = (
            (await session.execute(select(Incident).where(Incident.reference == reference)))
            .scalars()
            .one()
        )
        assert incident.prediction_id == predictions[0].id


async def test_connections_and_crew_are_real_service_output(client, sessionmaker_for):
    """Per-incident scope: this flight's own numbers, from the services themselves."""
    reference = await _open_incident(sessionmaker_for, PRIMARY_FLIGHT_ID)
    _drive_to_terminal(client, reference)

    async with sessionmaker_for() as session:
        actions = (await session.execute(select(Action))).scalars().all()

        connections = next(a for a in actions if a.payload and "at_risk_count" in a.payload)
        assert connections.status == ActionStatus.success
        assert connections.payload["rule_version"] == "connection-v1"
        assert connections.payload["at_risk_count"] == 8  # 6E 2134's own connections
        assert connections.payload["minimum_connection_minutes"] == 45
        sample = connections.payload["at_risk"][0]
        assert sample["passenger_reference"].startswith("PAX-")
        assert sample["shortfall_minutes"] < 0

        crew = next(a for a in actions if a.payload and "pairings_at_risk" in a.payload)
        assert crew.status == ActionStatus.success
        assert crew.payload["rule_version"] == "crew-impact-v1"
        # 6E 2134 carries two rotations: cockpit and a separate cabin pairing.
        assert crew.payload["pairings_at_risk"] == 2
        assert crew.payload["mechanism_counts"] == {"operating": 1, "second_pairing": 1}

        notified = next(a for a in actions if a.payload and "real_count" in a.payload)
        assert notified.payload["real_count"] == 0
        assert notified.payload["simulated_count"] == 174
        assert notified.payload["not_rendered"] == []


# ------------------------------------------------------- the cascade, at group scope


async def test_the_full_cascade_reaches_the_verified_group_values(client, sessionmaker_for):
    """Eight incidents in one group, each run through the real app, then rolled up.

    This is where 22 and 9 live. They are group figures: `connections_at_risk` is the union
    of the bookings the connection service actually reported, and `crew_pairings_affected` is
    the number of distinct rotations across the group's crew assessments. Neither is
    recomputed here and neither is asserted into existence — an incident that had not run
    would simply contribute nothing.
    """
    references = [
        await _open_incident(sessionmaker_for, flight_id) for flight_id in AFFECTED_FLIGHT_IDS
    ]
    for reference in references:
        state = _drive_to_terminal(client, reference)
        assert state["state"] == IncidentState.resolved.value, (
            f"{reference} stopped in {state['state']}: {state.get('note')}"
        )

    async with sessionmaker_for() as session:
        group = (
            (
                await session.execute(
                    select(IncidentGroup).where(IncidentGroup.reference == INCIDENT_GROUP_REFERENCE)
                )
            )
            .scalars()
            .one()
        )
        rollup = await cascade_rollup(session, group_id=group.id)

    assert rollup.flights_affected == 8
    assert rollup.passengers_affected == EXPECTED_PASSENGERS
    assert rollup.connections_at_risk == EXPECTED_CONNECTIONS
    assert rollup.crew_pairings_affected == EXPECTED_PAIRINGS
    assert rollup.candidate_hotels == EXPECTED_HOTELS
    assert rollup.is_complete is True


async def test_the_group_count_is_a_union_not_a_sum(client, sessionmaker_for):
    """22 is the number of distinct bookings, so it cannot be inflated by double counting."""
    references = [
        await _open_incident(sessionmaker_for, flight_id) for flight_id in AFFECTED_FLIGHT_IDS
    ]
    for reference in references:
        _drive_to_terminal(client, reference)

    async with sessionmaker_for() as session:
        group = (
            (
                await session.execute(
                    select(IncidentGroup).where(IncidentGroup.reference == INCIDENT_GROUP_REFERENCE)
                )
            )
            .scalars()
            .one()
        )
        rollup = await cascade_rollup(session, group_id=group.id)

    assert len(rollup.at_risk_booking_ids) == len(set(rollup.at_risk_booking_ids))
    assert len(rollup.at_risk_booking_ids) == EXPECTED_CONNECTIONS


async def test_every_mechanism_appears_and_each_pairing_has_exactly_one(client, sessionmaker_for):
    """The cascade graph's edge labels, after group-level deduplication."""
    references = [
        await _open_incident(sessionmaker_for, flight_id) for flight_id in AFFECTED_FLIGHT_IDS
    ]
    for reference in references:
        _drive_to_terminal(client, reference)

    async with sessionmaker_for() as session:
        group = (
            (
                await session.execute(
                    select(IncidentGroup).where(IncidentGroup.reference == INCIDENT_GROUP_REFERENCE)
                )
            )
            .scalars()
            .one()
        )
        rollup = await cascade_rollup(session, group_id=group.id)

    references_seen = [pairing.pairing_reference for pairing in rollup.pairings]
    assert len(references_seen) == len(set(references_seen)) == EXPECTED_PAIRINGS
    assert {pairing.mechanism for pairing in rollup.pairings} == {
        "operating",
        "onward_duty",
        "second_pairing",
        "positioning",
    }

    # PAIR-E1 is reached from both UK 705 and UK 812. `onward_duty` names the leg that
    # actually fails, so it must win over `operating`.
    e1 = next(p for p in rollup.pairings if p.pairing_reference == "PAIR-E1")
    assert e1.mechanism == "onward_duty"


async def test_the_recursive_sql_agrees_with_the_services(client, sessionmaker_for):
    """`docs/22-crew-pairing-model.md` promises the nine can be counted in SQL. It can."""
    references = [
        await _open_incident(sessionmaker_for, flight_id) for flight_id in AFFECTED_FLIGHT_IDS
    ]
    for reference in references:
        _drive_to_terminal(client, reference)

    async with sessionmaker_for() as session:
        group = (
            (
                await session.execute(
                    select(IncidentGroup).where(IncidentGroup.reference == INCIDENT_GROUP_REFERENCE)
                )
            )
            .scalars()
            .one()
        )
        rollup = await cascade_rollup(session, group_id=group.id)
        walked = await affected_pairings_recursive(session, set(AFFECTED_FLIGHT_IDS))

    assert len(walked) == rollup.crew_pairings_affected == EXPECTED_PAIRINGS
    assert {row["reference"] for row in walked} == {
        pairing.pairing_reference for pairing in rollup.pairings
    }


async def test_a_partial_cascade_is_reported_as_partial(client, sessionmaker_for):
    """Three of eight incidents worked is not a complete answer and must not read as one."""
    for flight_id in AFFECTED_FLIGHT_IDS:
        await _open_incident(sessionmaker_for, flight_id)

    async with sessionmaker_for() as session:
        group = (
            (
                await session.execute(
                    select(IncidentGroup).where(IncidentGroup.reference == INCIDENT_GROUP_REFERENCE)
                )
            )
            .scalars()
            .one()
        )
        rollup = await cascade_rollup(session, group_id=group.id)

    assert rollup.incidents_in_group == 8
    assert rollup.incidents_assessed_connections == 0
    assert rollup.connections_at_risk == 0
    assert rollup.crew_pairings_affected == 0
    assert rollup.is_complete is False
    # The dataset facts are still true and still derived from records.
    assert rollup.passengers_affected == EXPECTED_PASSENGERS
    assert rollup.flights_affected == 8


# --------------------------------------------------------- registration and determinism


def test_only_stream_as_registration_path_exists(registered):
    """One seam. A second adapter layer is how two callers start disagreeing about scope."""
    import app.orchestrator.service_registry as seam

    assert set(registered) == {action.value for action in seam.STAGE2_ADAPTERS}
    for action in seam.STAGE2_ADAPTERS:
        assert dispatch.is_implemented(action)
        assert dispatch.ACTION_OWNERS[action]


def test_no_second_registry_module_remains():
    """`app/services/registry.py` was Stream C's duplicate of the same seam. It is gone."""
    from importlib.util import find_spec

    assert find_spec("app.services.registry") is None


def test_unimplemented_actions_still_refuse(registered):
    for action in (
        ActionType.find_hotel_options,
        ActionType.reserve_hotel_block,
        ActionType.evaluate_entitlements,
        ActionType.record_outcome,
    ):
        assert not dispatch.is_implemented(action)


async def test_the_seeded_dataset_is_untouched_by_a_full_run(client, sessionmaker_for):
    """The workflow appends records. It must not mutate the dataset it reasons over."""
    from app.db.seed import dataset_counts, plan_digest

    async with sessionmaker_for() as session:
        before = await dataset_counts(session)
    digest_before = plan_digest()

    reference = await _open_incident(sessionmaker_for, PRIMARY_FLIGHT_ID)
    _drive_to_terminal(client, reference)

    async with sessionmaker_for() as session:
        assert await dataset_counts(session) == before
    assert plan_digest() == digest_before


async def test_a_second_identical_run_changes_nothing(client, sessionmaker_for):
    """Idempotency at the API surface: the recorded action is replayed, not repeated."""
    reference = await _open_incident(sessionmaker_for, PRIMARY_FLIGHT_ID)
    _drive_to_terminal(client, reference)

    async with sessionmaker_for() as session:
        first = [
            (a.idempotency_key, a.status, a.reason)
            for a in (await session.execute(select(Action).order_by(Action.id))).scalars()
        ]

    _run(client, reference)

    async with sessionmaker_for() as session:
        second = [
            (a.idempotency_key, a.status, a.reason)
            for a in (await session.execute(select(Action).order_by(Action.id))).scalars()
        ]
    assert first == second
