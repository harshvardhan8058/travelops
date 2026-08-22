"""The disruption graph, the rollup snapshot, the composed blast radius and what-if.

Built over the committed dataset with recorded actions inserted directly, because the subject
here is the *projection*, not the orchestrator. That separation is deliberate: if these tests
went through the engine, a change in playbook ordering could fail them, and the failure would say
nothing about whether the graph is faithful to the rows behind it.

The governing property, asserted from several directions: **the graph and the rollup are two
renderings of one set of facts, and they cannot disagree.** A cascade picture that shows nine
pairings beside a headline of eight would end the demo, and no amount of explanation afterwards
would recover it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.db.scenario_queries import cascade_rollup, group_affected_flights
from app.db.seed import INCIDENT_GROUP_REFERENCE, seed_demo_dataset
from app.models.cascade import CascadeSnapshot, DisruptionEdge
from app.models.enums import (
    ActionStatus,
    ActionType,
    AssuranceDecision,
    IncidentState,
    ProvenanceKind,
    RiskLevel,
    RiskTier,
    TriggerType,
)
from app.models.workflow import (
    Action,
    AssuranceEvaluation,
    Incident,
    IncidentGroup,
    Plan,
    PlanTask,
    Prediction,
)
from app.services.blast_radius import blast_radius_payload, compose_blast_radius
from app.services.cascade_graph import (
    EDGE_CONNECTION,
    EDGE_CREW,
    EDGE_ROOT_CAUSE,
    NODE_FLIGHT,
    graph_payload,
    persist_edges,
    project_and_record,
    project_graph,
    snapshot_hash,
    write_snapshot,
)
from app.services.what_if import ALLOWED_LEVERS, evaluate_what_if, what_if_payload
from tests.contract.sqlite_support import create_sqlite_engine

pytestmark = pytest.mark.anyio

#: Two pairings and two broken bookings per flight. Small, and enough to prove every property:
#: PAIR-SHARED is deliberately reported by two flights so deduplication is exercised.
CREW_PER_FLIGHT = 2


async def _open_incidents(session, group_id: int) -> dict[int, int]:
    """One incident per declared member flight. Returns flight_id -> incident_id."""
    members = await group_affected_flights(session, group_id=group_id)
    mapping: dict[int, int] = {}
    for index, member in enumerate(members, start=1):
        incident = Incident(
            reference=f"INC-TEST-{index:03d}",
            flight_id=member.flight_id,
            group_id=group_id,
            trigger_type=TriggerType.weather,
            severity="high",
            state=IncidentState.executing,
            opened_at=func.now(),
            demo_dataset_id="bengaluru_storm",
        )
        session.add(incident)
        await session.flush()
        mapping[member.flight_id] = incident.id
    return mapping


async def _record_action(
    session, *, incident_id: int, action_type: ActionType, payload: dict
) -> int:
    """A successful action with the assurance evaluation it is required to reference."""
    plan = Plan(
        incident_id=incident_id,
        generated_at=func.now(),
        generator="test-fixture",
        retrieved_incident_ids=[],
    )
    session.add(plan)
    await session.flush()

    task = PlanTask(
        plan_id=plan.id,
        action_type=action_type,
        task_order=1,
        depends_on=[],
        target_refs=[],
        inputs={},
        state="done",
    )
    session.add(task)
    await session.flush()

    evaluation = AssuranceEvaluation(
        plan_task_id=task.id,
        decision=AssuranceDecision.execute,
        risk_tier=RiskTier.low,
        check_results=[],
        blocking_reasons=[],
        evidence_refs=[],
        config_version="test",
        config_hash="test",
    )
    session.add(evaluation)
    await session.flush()

    action = Action(
        plan_task_id=task.id,
        assurance_id=evaluation.id,
        actor="test-fixture",
        idempotency_key=f"test-{incident_id}-{action_type.value}",
        status=ActionStatus.success,
        reason="recorded by the test fixture",
        provenance_kind=ProvenanceKind.synthetic,
        payload=payload,
    )
    session.add(action)
    await session.flush()
    return action.id


@pytest.fixture
async def worked(tmp_path):
    """A seeded dataset with predictions and recorded findings for every declared flight."""
    engine = create_sqlite_engine(tmp_path / "cascade_graph.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        await seed_demo_dataset(session)
        group = (
            (
                await session.execute(
                    select(IncidentGroup).where(IncidentGroup.reference == INCIDENT_GROUP_REFERENCE)
                )
            )
            .scalars()
            .one()
        )
        incidents = await _open_incidents(session, group.id)

        booking_seq = 1
        for order, (flight_id, incident_id) in enumerate(sorted(incidents.items())):
            session.add(
                Prediction(
                    flight_id=flight_id,
                    airport_icao=group.airport_icao,
                    risk_index=80,
                    risk_level=RiskLevel.severe,
                    rule_version="delay-risk-v1",
                    factors=[],
                    evidence_refs=[],
                )
            )
            await session.flush()

            impacts = [
                {
                    "pairing_id": 1000 + order * CREW_PER_FLIGHT + n,
                    "pairing_reference": f"PAIR-T{order}{n}",
                    "base_icao": "VOBL",
                    "source_flight_number": f"XX {flight_id}",
                    "affected_leg_order": 1,
                    "pairing_leg_count": 2,
                    "mechanism": "operating",
                    "detail": "recorded by the test fixture",
                    "is_at_risk": True,
                    "depth": 1,
                }
                for n in range(CREW_PER_FLIGHT)
            ]
            # One rotation seen from two flights, so deduplication is genuinely exercised.
            impacts.append(
                {
                    "pairing_id": 9999,
                    "pairing_reference": "PAIR-SHARED",
                    "base_icao": "VOBL",
                    "source_flight_number": f"XX {flight_id}",
                    "affected_leg_order": 2,
                    "pairing_leg_count": 2,
                    "mechanism": "onward_duty",
                    "detail": "the same rotation, reached from two flights",
                    "is_at_risk": True,
                    "depth": 1,
                }
            )
            await _record_action(
                session,
                incident_id=incident_id,
                action_type=ActionType.assess_crew_impact,
                payload={"impacts": impacts},
            )

            at_risk = []
            for _ in range(2):
                at_risk.append(
                    {
                        "booking_id": booking_seq,
                        "pnr": f"PNR{booking_seq:04d}",
                        "onward_flight_number": "XX 999",
                        "detail": "onward segment no longer connects",
                    }
                )
                booking_seq += 1
            await _record_action(
                session,
                incident_id=incident_id,
                action_type=ActionType.check_connections,
                payload={"at_risk": at_risk},
            )

            # A recorded accommodation requirement, so the what-if has a room basis to
            # re-evaluate. Without one, "rooms required" is legitimately zero and the lever tests
            # below would be asserting against a figure no service ever produced.
            await _record_action(
                session,
                incident_id=incident_id,
                action_type=ActionType.reserve_hotel_block,
                payload={
                    "rooms_required": 10,
                    "rooms_allocated": 4,
                    "shortfall_rooms": 6,
                    "total_cost_inr": 12000,
                    "allocations": [
                        {
                            "hotel_id": 1,
                            "hotel_name": "Airport Transit Inn",
                            "rooms": 4,
                            "rate_inr": 2500,
                            "nights": 1,
                            "cost_inr": 10000,
                            "is_partner": True,
                            "distance_km": 1.5,
                            "detail": "recorded by the test fixture",
                        }
                    ],
                },
            )

        await session.commit()
        yield session, group.id
    await engine.dispose()


# ------------------------------------------------------------------- graph faithfulness


async def test_every_declared_flight_is_a_node(worked):
    """Eight, not seven, and not "however many had actions". Membership drives the nodes."""
    session, group_id = worked
    graph = await project_graph(session, group_id=group_id)
    flight_nodes = [n for n in graph.nodes if n.kind == NODE_FLIGHT]
    assert len(flight_nodes) == 8
    assert graph.member_flight_count == 8
    assert {n.ref for n in flight_nodes} == {f"flight:{n}" for n in (1, 2, 3, 5, 6, 7, 8, 9)}


async def test_exactly_one_flight_node_is_marked_primary(worked):
    session, group_id = worked
    graph = await project_graph(session, group_id=group_id)
    primaries = [n for n in graph.nodes if n.kind == NODE_FLIGHT and n.role == "primary"]
    assert len(primaries) == 1
    assert primaries[0].ref == "flight:1"


async def test_the_arrival_is_declared_as_an_arrival(worked):
    """UK 705 arrives into VOBL. If it were rendered as a departure the picture would be
    wrong in exactly the way a departure-origin query would have made it wrong."""
    session, group_id = worked
    graph = await project_graph(session, group_id=group_id)
    arrivals = [n for n in graph.nodes if n.role == "affected_arrival"]
    assert len(arrivals) == 1
    assert arrivals[0].ref == "flight:9"


async def test_the_graph_pairing_count_equals_the_rollup_pairing_count(worked):
    """The assertion that matters most. Two renderings of one set of facts."""
    session, group_id = worked
    graph = await project_graph(session, group_id=group_id)
    rollup = await cascade_rollup(session, group_id=group_id)
    pairing_nodes = {n.ref for n in graph.nodes if n.kind == "pairing"}
    assert len(pairing_nodes) == rollup.crew_pairings_affected


async def test_the_graph_booking_count_equals_the_rollup_connection_count(worked):
    """`connections_at_risk` is a union of bookings; the graph must draw that same union."""
    session, group_id = worked
    graph = await project_graph(session, group_id=group_id)
    rollup = await cascade_rollup(session, group_id=group_id)
    booking_nodes = {n.ref for n in graph.nodes if n.kind == "booking"}
    assert len(booking_nodes) == rollup.connections_at_risk


async def test_a_rotation_reached_from_two_flights_is_one_node_with_two_edges(worked):
    """Deduplicated as a node, because it is one rotation. Not deduplicated as an edge,
    because both flights genuinely touch it and hiding one would lose a cause."""
    session, group_id = worked
    graph = await project_graph(session, group_id=group_id)
    shared = [n for n in graph.nodes if n.ref == "pairing:9999"]
    assert len(shared) == 1
    edges = [e for e in graph.edges if e.target_ref == "pairing:9999"]
    assert len(edges) == 8


# ----------------------------------------------------------------------- edge provenance


async def test_every_edge_names_exactly_one_evidence_row(worked):
    """The property that separates this from a diagram."""
    session, group_id = worked
    graph = await project_graph(session, group_id=group_id)
    assert graph.edges
    for edge in graph.edges:
        has_action = edge.derived_from_action_id is not None
        has_prediction = edge.derived_from_prediction_id is not None
        assert has_action != has_prediction, edge
        assert edge.evidence_ref.startswith(("action:", "prediction:"))


async def test_root_cause_edges_come_from_predictions_and_the_rest_from_actions(worked):
    """The weather is not an action anyone took, so a root-cause edge cannot name one."""
    session, group_id = worked
    graph = await project_graph(session, group_id=group_id)
    for edge in graph.edges_of(EDGE_ROOT_CAUSE):
        assert edge.derived_from_prediction_id is not None
        assert edge.derived_from_action_id is None
    for kind in (EDGE_CREW, EDGE_CONNECTION):
        for edge in graph.edges_of(kind):
            assert edge.derived_from_action_id is not None


async def test_one_root_cause_edge_per_flight_even_after_a_second_assessment(worked):
    """A re-assessment is the same cause looked at again, not a second cause."""
    session, group_id = worked
    session.add(
        Prediction(
            flight_id=1,
            airport_icao="VOBL",
            risk_index=90,
            risk_level=RiskLevel.severe,
            rule_version="delay-risk-v1",
            factors=[],
            evidence_refs=[],
        )
    )
    await session.flush()
    graph = await project_graph(session, group_id=group_id)
    for_flight_one = [e for e in graph.edges_of(EDGE_ROOT_CAUSE) if e.target_ref == "flight:1"]
    assert len(for_flight_one) == 1


async def test_an_unassessed_flight_is_a_visible_gap_not_a_missing_node(worked):
    """A cascade that hides its unworked flights looks finished when it is not."""
    session, group_id = worked
    await session.execute(Prediction.__table__.delete().where(Prediction.flight_id == 9))
    await session.execute(
        Incident.__table__.update().where(Incident.flight_id == 9).values(group_id=None)
    )
    await session.flush()

    graph = await project_graph(session, group_id=group_id)
    assert graph.member_flight_count == 8
    assert graph.flights_with_evidence == 7
    assert graph.is_complete is False
    assert "7 of 8" in graph.completeness_note
    unworked = next(n for n in graph.nodes if n.ref == "flight:9")
    assert unworked.has_evidence is False


# --------------------------------------------------------------------- persisted edges


async def test_persisting_twice_does_not_multiply_the_graph(worked):
    """A dashboard that reprojects on every poll must not grow the edge table."""
    session, group_id = worked
    graph = await project_graph(session, group_id=group_id)

    first = await persist_edges(session, group_id=group_id, graph=graph)
    second = await persist_edges(session, group_id=group_id, graph=graph)
    assert first == len(graph.edges)
    assert second == 0

    stored = (
        await session.execute(
            select(func.count())
            .select_from(DisruptionEdge)
            .where(DisruptionEdge.incident_group_id == group_id)
        )
    ).scalar_one()
    assert stored == len(graph.edges)


async def test_persisted_edges_carry_their_evidence_column(worked):
    session, group_id = worked
    graph = await project_graph(session, group_id=group_id)
    await persist_edges(session, group_id=group_id, graph=graph)

    rows = (
        (
            await session.execute(
                select(DisruptionEdge).where(DisruptionEdge.incident_group_id == group_id)
            )
        )
        .scalars()
        .all()
    )
    assert rows
    for row in rows:
        assert (row.derived_from_action_id is None) != (row.derived_from_prediction_id is None)


# -------------------------------------------------------------------------- snapshots


async def test_the_snapshot_hash_is_stable_for_unchanged_facts(worked):
    session, group_id = worked
    graph = await project_graph(session, group_id=group_id)
    rollup = await cascade_rollup(session, group_id=group_id)
    assert snapshot_hash(rollup=rollup, graph=graph) == snapshot_hash(rollup=rollup, graph=graph)


async def test_the_snapshot_hash_moves_when_a_figure_moves(worked):
    """A changed number must be a changed hash, or a snapshot proves nothing."""
    session, group_id = worked
    graph = await project_graph(session, group_id=group_id)
    rollup = await cascade_rollup(session, group_id=group_id)
    before = snapshot_hash(rollup=rollup, graph=graph)
    moved = rollup.model_copy(update={"connections_at_risk": rollup.connections_at_risk + 1})
    assert snapshot_hash(rollup=moved, graph=graph) != before


async def test_snapshots_are_append_only_and_record_their_sources(worked):
    """History, not a mutable rollup column. Two computations leave two rows."""
    session, group_id = worked
    graph = await project_graph(session, group_id=group_id)
    rollup = await cascade_rollup(session, group_id=group_id)

    first = await write_snapshot(session, group_id=group_id, rollup=rollup, graph=graph)
    second = await write_snapshot(session, group_id=group_id, rollup=rollup, graph=graph)
    assert first.id != second.id
    assert first.snapshot_hash == second.snapshot_hash
    assert first.source_action_ids == graph.source_action_ids
    assert first.payload["edge_count"] == len(graph.edges)

    count = (
        await session.execute(
            select(func.count())
            .select_from(CascadeSnapshot)
            .where(CascadeSnapshot.incident_group_id == group_id)
        )
    ).scalar_one()
    assert count == 2


async def test_the_snapshot_figures_equal_the_rollup_it_was_taken_from(worked):
    """A snapshot that could disagree with the derivation would be a second source of truth."""
    session, group_id = worked
    _graph, rollup, snapshot, _inserted = await project_and_record(session, group_id=group_id)
    assert snapshot.flights_affected == rollup.flights_affected == 8
    assert snapshot.passengers_affected == rollup.passengers_affected == 604
    assert snapshot.connections_at_risk == rollup.connections_at_risk
    assert snapshot.crew_pairings_affected == rollup.crew_pairings_affected
    assert snapshot.candidate_hotels == rollup.candidate_hotels == 11
    assert snapshot.is_complete == rollup.is_complete


async def test_no_rollup_column_was_added_to_incident_group():
    """Guards the decision. Denormalising the rollup onto the group would let a cached figure
    drift from the rows it summarises, with nothing to say which is right."""
    columns = {c.name for c in IncidentGroup.__table__.columns}
    for forbidden in (
        "flights_affected",
        "passengers_affected",
        "connections_at_risk",
        "crew_pairings_affected",
        "candidate_hotels",
    ):
        assert forbidden not in columns


# ------------------------------------------------------------------------ blast radius


async def test_blast_radius_repeats_the_rollup_and_invents_nothing(worked):
    session, group_id = worked
    graph, rollup, _snapshot, _n = await project_and_record(session, group_id=group_id)
    radius = compose_blast_radius(rollup=rollup, graph=graph)

    assert radius.value_of("flights") == rollup.flights_affected
    assert radius.value_of("passengers") == rollup.passengers_affected
    assert radius.value_of("connections") == rollup.connections_at_risk
    assert radius.value_of("crew_pairings") == rollup.crew_pairings_affected
    assert radius.value_of("candidate_hotels") == rollup.candidate_hotels


async def test_every_blast_radius_dimension_names_what_measured_it(worked):
    session, group_id = worked
    graph, rollup, _s, _n = await project_and_record(session, group_id=group_id)
    for dimension in compose_blast_radius(rollup=rollup, graph=graph).dimensions:
        assert dimension.measured_by
        assert dimension.unit


async def test_blast_radius_reports_completeness_and_never_confidence(worked):
    """Completeness is countable. Confidence would be a probability nothing here is calibrated
    to produce, and one uncheckable figure discredits the checkable ones beside it."""
    session, group_id = worked
    graph, rollup, _s, _n = await project_and_record(session, group_id=group_id)
    payload = blast_radius_payload(compose_blast_radius(rollup=rollup, graph=graph))
    assert "completeness" in payload
    assert payload["basis"] == "composed_from_recorded_findings"
    flat = str(payload).lower()
    assert "confidence" not in flat
    assert "probability" not in flat


async def test_an_incomplete_blast_radius_says_so_in_the_headline(worked):
    """A partial answer must not be renderable as a complete one, which is why the caveat is
    inside the same string as the totals."""
    session, group_id = worked
    await session.execute(
        Incident.__table__.update().where(Incident.flight_id == 9).values(group_id=None)
    )
    await session.flush()

    graph, rollup, _s, _n = await project_and_record(session, group_id=group_id)
    radius = compose_blast_radius(rollup=rollup, graph=graph)
    assert radius.is_complete is False
    assert "floors rather than totals" in radius.headline
    assert any("no incident open" in gap for gap in radius.gaps)


# ------------------------------------------------------------------------------ what-if


async def _row_census(session) -> dict[str, int]:
    """Every table's row count. The zero-write proof."""
    census: dict[str, int] = {}
    for name, table in sorted(Base.metadata.tables.items()):
        census[name] = int(
            (await session.execute(select(func.count()).select_from(table))).scalar_one()
        )
    return census


async def test_what_if_writes_absolutely_nothing(worked):
    """P2-D2's hard boundary, proved by counting every row in every table.

    A what-if that left a trace would corrupt the evidence trail it exists to explore, and the
    figure a controller trusted would depend on whether someone had clicked "what if" first.
    """
    session, group_id = worked
    before = await _row_census(session)
    result = await evaluate_what_if(
        session,
        group_id=group_id,
        levers={
            "delay_minutes_by_flight": {1: 30},
            "minimum_connection_minutes": 30,
            "passengers_per_room": 3,
            "max_rate_inr": 9999,
            "max_expansion_depth": 3,
        },
    )
    after = await _row_census(session)
    assert after == before
    assert result.wrote_rows is False
    assert result.basis == "recorded_evidence"


async def test_what_if_refuses_an_undeclared_lever_by_name(worked):
    """An open parameter bag would let a caller reach into a ruleset, get a favourable answer,
    and present it with the authority of a real assessment."""
    session, group_id = worked
    result = await evaluate_what_if(
        session, group_id=group_id, levers={"crew_duty_limit_hours": 14}
    )
    assert [item.lever for item in result.levers_rejected] == ["crew_duty_limit_hours"]
    assert "not a what-if lever" in result.levers_rejected[0].reason
    assert result.levers_applied == {}


async def test_what_if_with_no_levers_changes_nothing_and_says_nothing_changed(worked):
    session, group_id = worked
    result = await evaluate_what_if(session, group_id=group_id, levers={})
    assert result.deltas == []
    assert "No recognised lever" in result.headline


async def test_raising_the_rate_cap_reduces_the_room_shortfall(worked):
    """A real re-evaluation, not a stub: the same allocator over a substituted cap."""
    session, group_id = worked
    result = await evaluate_what_if(session, group_id=group_id, levers={"max_rate_inr": 20000})
    shortfall = next(d for d in result.deltas if d.key == "rooms_short")
    assert shortfall.baseline > 0
    assert shortfall.scenario < shortfall.baseline
    assert "fewer" in shortfall.summary


async def test_doubling_up_occupancy_reduces_the_rooms_required(worked):
    session, group_id = worked
    result = await evaluate_what_if(session, group_id=group_id, levers={"passengers_per_room": 4})
    required = next(d for d in result.deltas if d.key == "rooms_required")
    assert required.scenario < required.baseline


async def test_the_what_if_payload_states_the_boundary_and_the_levers(worked):
    """The boundary is the thing most likely to be over-read on a screen, by exactly the
    audience most likely to over-read it."""
    session, group_id = worked
    result = await evaluate_what_if(
        session, group_id=group_id, levers={"minimum_connection_minutes": 30}
    )
    payload = what_if_payload(result)
    assert payload["wrote_rows"] is False
    assert "Not a simulation" in payload["boundary_note"]
    assert "digital twin" in payload["boundary_note"]
    assert set(payload["levers_available"]) == set(ALLOWED_LEVERS)


async def test_what_if_is_deterministic(worked):
    session, group_id = worked
    levers = {"minimum_connection_minutes": 30, "max_rate_inr": 9999}
    first = await evaluate_what_if(session, group_id=group_id, levers=dict(levers))
    second = await evaluate_what_if(session, group_id=group_id, levers=dict(levers))
    assert first.model_dump() == second.model_dump()


# ------------------------------------------------------------------------ render shape


async def test_the_graph_payload_carries_everything_a_renderer_needs(worked):
    """Stream D should never have to compute anything to draw this."""
    session, group_id = worked
    graph = await project_graph(session, group_id=group_id)
    rollup = await cascade_rollup(session, group_id=group_id)
    payload = graph_payload(graph, rollup)

    assert set(payload) >= {
        "group_reference",
        "nodes",
        "edges",
        "edge_counts_by_kind",
        "completeness",
        "snapshot_hash",
    }
    assert payload["completeness"]["member_flight_count"] == 8
    assert payload["edge_counts_by_kind"]["root_cause"] == 8
    for node in payload["nodes"]:
        assert node["label"]
