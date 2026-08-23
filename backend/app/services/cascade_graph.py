"""The disruption graph — STREAM C (C2-1, C2-2).

Projects the cascade as a graph over rows that already exist, and records the projection so a
figure on screen can be reconstructed later.

The design decision that shapes everything else: **there is no node table.** Nodes are
addressed `kind:identifier` — the same vocabulary as `evidence_refs` and `target_refs` — so a
node *is* a `flight`, `pairing` or `booking` row. A node table would duplicate those rows and
need syncing, and the first time it drifted, the graph and the incident list would disagree
about the same disruption with nothing to say which was right.

The second decision: **every edge names the recorded row it came from.** That is what
separates this from a diagram. An edge with no evidence behind it is an assertion; with one, a
reviewer can open the row and read the payload that produced it. It also means the graph cannot
draw an edge the services never found — the shape of the picture is bounded by recorded
evidence, so an incomplete cascade looks incomplete.

Root-cause edges take their evidence from a `prediction` rather than an `action`, because the
weather is not an action anyone took. `event:GRP-... -> flight:N` is attributed to that
flight's delay-risk assessment: the recorded row tying this flight to the disruption. Where
risk was never assessed, no root-cause edge is drawn and the flight appears as a node with no
incoming edge — a visible gap instead of an invented link.

Owner: Stream C. Stream A calls `project_and_record`; Stream D renders `CascadeGraph`.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.scenario_queries import (
    CascadeRollup,
    cascade_rollup,
    group_affected_flights,
    recorded_actions,
)
from app.models.cascade import CascadeSnapshot, DisruptionEdge

RULE_VERSION = "cascade-graph-v1"

#: Node kinds. Deliberately closed: a new kind is a schema conversation, not a quiet addition.
NODE_EVENT = "event"
NODE_FLIGHT = "flight"
NODE_PAIRING = "pairing"
NODE_BOOKING = "booking"
NODE_HOTEL = "hotel"

EDGE_ROOT_CAUSE = "root_cause"
EDGE_CREW = "crew"
EDGE_CONNECTION = "connection"
EDGE_ACCOMMODATION = "accommodation"


class GraphNode(BaseModel):
    """A node is a reference to a row that already exists, plus display facts read off it."""

    model_config = ConfigDict(extra="forbid")

    ref: str
    kind: str
    label: str
    sublabel: str | None = None
    #: Distance from the root event in hops. The event is 0.
    depth: int = 1
    at_risk: bool = True
    #: False when the node is declared but no service has looked at it yet. Rendered as a
    #: gap, never quietly dropped: a cascade that hides its unworked flights looks finished.
    has_evidence: bool = True
    role: str | None = None


class GraphEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_ref: str
    target_ref: str
    edge_kind: str
    mechanism: str | None = None
    detail: str | None = None
    depth: int = 1
    #: Exactly one of these is set — action for crew/connection/accommodation, prediction for
    #: root cause. The CHECK constraint on `disruption_edge` enforces it in the database.
    derived_from_action_id: int | None = None
    derived_from_prediction_id: int | None = None

    @property
    def evidence_ref(self) -> str:
        if self.derived_from_action_id is not None:
            return f"action:{self.derived_from_action_id}"
        return f"prediction:{self.derived_from_prediction_id}"


class CascadeGraph(BaseModel):
    """A projection, not a stored structure. Recomputable from actions at any time."""

    model_config = ConfigDict(extra="forbid")

    group_reference: str
    rule_version: str = RULE_VERSION
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)

    #: Every action id that contributed an edge, ascending.
    source_action_ids: list[int] = Field(default_factory=list)
    #: Every prediction id behind a root-cause edge, ascending.
    source_prediction_ids: list[int] = Field(default_factory=list)

    #: Honest coverage, phrased as completeness rather than confidence. Confidence would
    #: imply a probability this system has no basis to state; completeness is countable.
    member_flight_count: int = 0
    flights_with_evidence: int = 0

    @property
    def is_complete(self) -> bool:
        return (
            self.member_flight_count > 0 and self.flights_with_evidence == self.member_flight_count
        )

    @property
    def completeness_note(self) -> str:
        if self.member_flight_count == 0:
            return "No flights are declared for this group, so there is nothing to project."
        if self.is_complete:
            return f"All {self.member_flight_count} declared flights carry recorded evidence."
        missing = self.member_flight_count - self.flights_with_evidence
        return (
            f"{self.flights_with_evidence} of {self.member_flight_count} declared flights "
            f"carry recorded evidence. {missing} have not been assessed yet, so this graph is "
            "a partial view of the cascade."
        )

    def edges_of(self, edge_kind: str) -> list[GraphEdge]:
        return [edge for edge in self.edges if edge.edge_kind == edge_kind]


def _flight_ref(flight_id: int | str) -> str:
    return f"{NODE_FLIGHT}:{flight_id}"


async def _latest_predictions(
    session: AsyncSession, flight_ids: list[int]
) -> dict[int, tuple[int, int, str]]:
    """flight_id -> (prediction_id, risk_index, risk_level) for the most recent assessment.

    Latest only. Two predictions for one flight are a re-assessment, not two causes, and
    drawing both would double the root-cause edges without adding a fact.
    """
    from app.models.workflow import Prediction

    if not flight_ids:
        return {}

    rows = (
        await session.execute(
            select(
                Prediction.flight_id,
                Prediction.id,
                Prediction.risk_index,
                Prediction.risk_level,
            )
            .where(Prediction.flight_id.in_(flight_ids))
            .order_by(Prediction.flight_id, Prediction.predicted_at, Prediction.id)
        )
    ).all()
    latest: dict[int, tuple[int, int, str]] = {}
    for flight_id, prediction_id, risk_index, risk_level in rows:
        latest[int(flight_id)] = (int(prediction_id), int(risk_index), str(risk_level))
    return latest


async def project_graph(session: AsyncSession, *, group_id: int) -> CascadeGraph:
    """Build the graph from declared membership and recorded actions. Reads only.

    Nodes come from membership (so every declared flight appears, worked or not); edges come
    exclusively from action payloads. Those two facts together are what make the picture both
    complete in its flights and honest about its evidence.
    """
    from app.models.enums import ActionType

    members = await group_affected_flights(session, group_id=group_id)
    if not members:
        rollup = await cascade_rollup(session, group_id=group_id)
        return CascadeGraph(group_reference=rollup.group_reference)

    rollup = await cascade_rollup(session, group_id=group_id)
    incident_ids = sorted(
        member.incident_id for member in members if member.incident_id is not None
    )
    event_ref = f"{NODE_EVENT}:{rollup.group_reference}"

    nodes: dict[str, GraphNode] = {
        event_ref: GraphNode(
            ref=event_ref,
            kind=NODE_EVENT,
            label=rollup.group_reference,
            sublabel=f"Root cause at {rollup.airport_icao}",
            depth=0,
        )
    }
    for member in members:
        nodes[_flight_ref(member.flight_id)] = GraphNode(
            ref=_flight_ref(member.flight_id),
            kind=NODE_FLIGHT,
            label=member.flight_number,
            sublabel=(
                f"{member.origin_icao} -> {member.destination_icao}, "
                f"+{member.delay_minutes_at_injection} min"
            ),
            depth=1,
            role=member.role,
            has_evidence=False,
        )

    edges: list[GraphEdge] = []
    action_ids: set[int] = set()
    incident_flight = {
        member.incident_id: member.flight_id for member in members if member.incident_id is not None
    }

    # -------------------------------------------------------------- root cause (depth 1)
    # Evidence is the delay-risk prediction: the recorded row that ties this flight to the
    # weather. The latest prediction per flight wins, so a re-assessment supersedes rather
    # than adding a second parallel edge for the same claim.
    prediction_ids: set[int] = set()
    for flight_id, prediction in (
        await _latest_predictions(session, [member.flight_id for member in members])
    ).items():
        edges.append(
            GraphEdge(
                source_ref=event_ref,
                target_ref=_flight_ref(flight_id),
                edge_kind=EDGE_ROOT_CAUSE,
                detail=(
                    f"Delay risk {prediction[1]}/{prediction[2]} assessed against the "
                    "recorded weather and runway state."
                ),
                depth=1,
                derived_from_prediction_id=prediction[0],
            )
        )
        prediction_ids.add(prediction[0])
        nodes[_flight_ref(flight_id)].has_evidence = True

    # ------------------------------------------------------------------- crew (depth 2)
    for incident_id, action_id, payload in await recorded_actions(
        session, incident_ids, ActionType.assess_crew_impact.value
    ):
        flight_id = incident_flight.get(incident_id)
        if flight_id is None:
            continue
        for impact in payload.get("impacts") or []:
            if not impact.get("is_at_risk", True):
                continue
            ref = f"{NODE_PAIRING}:{impact['pairing_id']}"
            depth = int(impact.get("depth", 1)) + 1
            nodes.setdefault(
                ref,
                GraphNode(
                    ref=ref,
                    kind=NODE_PAIRING,
                    label=str(impact["pairing_reference"]),
                    sublabel=f"Base {impact['base_icao']}",
                    depth=depth,
                ),
            )
            edges.append(
                GraphEdge(
                    source_ref=_flight_ref(flight_id),
                    target_ref=ref,
                    edge_kind=EDGE_CREW,
                    mechanism=str(impact["mechanism"]),
                    detail=str(impact["detail"]),
                    depth=depth,
                    derived_from_action_id=action_id,
                )
            )
        action_ids.add(action_id)
        nodes[_flight_ref(flight_id)].has_evidence = True

    # ------------------------------------------------------------ connections (depth 2)
    for incident_id, action_id, payload in await recorded_actions(
        session, incident_ids, ActionType.check_connections.value
    ):
        flight_id = incident_flight.get(incident_id)
        if flight_id is None:
            continue
        for item in payload.get("at_risk") or []:
            booking_id = item.get("booking_id")
            if booking_id is None:
                continue
            ref = f"{NODE_BOOKING}:{booking_id}"
            nodes.setdefault(
                ref,
                GraphNode(
                    ref=ref,
                    kind=NODE_BOOKING,
                    label=str(item.get("pnr") or f"booking {booking_id}"),
                    sublabel=str(item.get("onward_flight_number") or "onward segment at risk"),
                    depth=2,
                ),
            )
            edges.append(
                GraphEdge(
                    source_ref=_flight_ref(flight_id),
                    target_ref=ref,
                    edge_kind=EDGE_CONNECTION,
                    mechanism="missed_connection",
                    detail=str(item.get("detail") or "Onward segment no longer connects."),
                    depth=2,
                    derived_from_action_id=action_id,
                )
            )
        action_ids.add(action_id)
        nodes[_flight_ref(flight_id)].has_evidence = True

    # --------------------------------------------------------- accommodation (depth 2)
    # Partial allocations included on purpose: a `needs_human` hotel action that secured 71 of
    # 87 rooms committed real inventory, and the edges are what make those rooms visible in the
    # cascade. The payload's `allocations` list is what is read, not the status.
    for incident_id, action_id, payload in await recorded_actions(
        session,
        incident_ids,
        ActionType.reserve_hotel_block.value,
        statuses=("success", "needs_human"),
    ):
        flight_id = incident_flight.get(incident_id)
        if flight_id is None:
            continue
        for allocation in payload.get("allocations") or []:
            hotel_id = allocation.get("hotel_id")
            if hotel_id is None:
                continue
            ref = f"{NODE_HOTEL}:{hotel_id}"
            nodes.setdefault(
                ref,
                GraphNode(
                    ref=ref,
                    kind=NODE_HOTEL,
                    label=str(allocation.get("hotel_name") or f"hotel {hotel_id}"),
                    sublabel=f"{allocation.get('rooms', 0)} rooms held",
                    depth=2,
                    at_risk=False,
                ),
            )
            edges.append(
                GraphEdge(
                    source_ref=_flight_ref(flight_id),
                    target_ref=ref,
                    edge_kind=EDGE_ACCOMMODATION,
                    mechanism="overnight_required",
                    detail=str(
                        allocation.get("detail")
                        or f"{allocation.get('rooms', 0)} rooms held for stranded passengers."
                    ),
                    depth=2,
                    derived_from_action_id=action_id,
                )
            )
        action_ids.add(action_id)
        nodes[_flight_ref(flight_id)].has_evidence = True

    flight_nodes = [node for node in nodes.values() if node.kind == NODE_FLIGHT]
    return CascadeGraph(
        group_reference=rollup.group_reference,
        nodes=sorted(nodes.values(), key=lambda node: (node.depth, node.kind, node.ref)),
        edges=sorted(
            edges,
            key=lambda edge: (edge.depth, edge.edge_kind, edge.source_ref, edge.target_ref),
        ),
        source_action_ids=sorted(action_ids),
        source_prediction_ids=sorted(prediction_ids),
        member_flight_count=len(flight_nodes),
        flights_with_evidence=sum(1 for node in flight_nodes if node.has_evidence),
    )


# ------------------------------------------------------------------------- persistence


def snapshot_hash(
    *, rollup: CascadeRollup, graph: CascadeGraph, rule_version: str = RULE_VERSION
) -> str:
    """Deterministic identity of a rollup computation.

    Over the figures and the action ids that produced them, not over timestamps — so replaying
    the same run reproduces the hash and a changed figure is immediately visible as a changed
    hash. 32 characters, so it fits on screen next to the numbers it certifies.
    """
    document = {
        "rule_version": rule_version,
        "group": rollup.group_reference,
        "figures": {
            "flights_affected": rollup.flights_affected,
            "passengers_affected": rollup.passengers_affected,
            "connections_at_risk": rollup.connections_at_risk,
            "candidate_hotels": rollup.candidate_hotels,
            "crew_pairings_affected": rollup.crew_pairings_affected,
        },
        "member_flight_ids": sorted(rollup.member_flight_ids),
        "source_action_ids": sorted(graph.source_action_ids),
        "source_prediction_ids": sorted(graph.source_prediction_ids),
        "edges": [
            [edge.edge_kind, edge.source_ref, edge.target_ref, edge.mechanism or ""]
            for edge in graph.edges
        ],
    }
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:32]


async def persist_edges(session: AsyncSession, *, group_id: int, graph: CascadeGraph) -> int:
    """Write the graph's edges, skipping any already recorded. Returns the number inserted.

    Idempotent by the unique constraint on
    `(incident_group_id, source_ref, target_ref, edge_kind, mechanism)`: re-running a
    projection must not multiply the graph. Checked in Python rather than relying on
    `ON CONFLICT` so the behaviour is identical on SQLite and Postgres.
    """
    existing = {
        (row.source_ref, row.target_ref, row.edge_kind, row.mechanism)
        for row in (
            await session.execute(
                select(DisruptionEdge).where(DisruptionEdge.incident_group_id == group_id)
            )
        )
        .scalars()
        .all()
    }

    inserted = 0
    for edge in graph.edges:
        key = (edge.source_ref, edge.target_ref, edge.edge_kind, edge.mechanism)
        if key in existing:
            continue
        session.add(
            DisruptionEdge(
                incident_group_id=group_id,
                source_ref=edge.source_ref,
                target_ref=edge.target_ref,
                edge_kind=edge.edge_kind,
                mechanism=edge.mechanism,
                detail=edge.detail,
                derived_from_action_id=edge.derived_from_action_id,
                derived_from_prediction_id=edge.derived_from_prediction_id,
                rule_version=graph.rule_version,
                depth=edge.depth,
                is_at_risk=True,
            )
        )
        existing.add(key)
        inserted += 1

    await session.flush()
    return inserted


async def write_snapshot(
    session: AsyncSession,
    *,
    group_id: int,
    rollup: CascadeRollup,
    graph: CascadeGraph,
) -> CascadeSnapshot:
    """Append a snapshot of the current rollup.

    Append-only, and never written back onto `incident_group`. A mutable rollup column drifts
    from the rows it summarises, and once it has, nothing in the system can say which of the
    two is correct. A history of computations can always be checked against the actions it
    names.
    """
    snapshot = CascadeSnapshot(
        incident_group_id=group_id,
        rule_version=graph.rule_version,
        snapshot_hash=snapshot_hash(rollup=rollup, graph=graph),
        flights_affected=rollup.flights_affected,
        passengers_affected=rollup.passengers_affected,
        connections_at_risk=rollup.connections_at_risk,
        candidate_hotels=rollup.candidate_hotels,
        crew_pairings_affected=rollup.crew_pairings_affected,
        incidents_in_group=rollup.incidents_in_group,
        incidents_assessed_connections=rollup.incidents_assessed_connections,
        incidents_assessed_crew=rollup.incidents_assessed_crew,
        is_complete=rollup.is_complete,
        source_action_ids=list(graph.source_action_ids),
        payload={
            "member_flight_ids": rollup.member_flight_ids,
            "flights_without_incident": rollup.flights_without_incident,
            "at_risk_booking_ids": rollup.at_risk_booking_ids,
            "node_count": len(graph.nodes),
            "edge_count": len(graph.edges),
            "edge_counts_by_kind": _edge_counts(graph),
            "completeness_note": graph.completeness_note,
        },
    )
    session.add(snapshot)
    await session.flush()
    return snapshot


def _edge_counts(graph: CascadeGraph) -> dict[str, int]:
    counts: dict[str, int] = {}
    for edge in graph.edges:
        counts[edge.edge_kind] = counts.get(edge.edge_kind, 0) + 1
    return counts


async def project_and_record(
    session: AsyncSession, *, group_id: int
) -> tuple[CascadeGraph, CascadeRollup, CascadeSnapshot, int]:
    """Project, persist the new edges, and append a snapshot. Returns all four results.

    The one entry point Stream A needs. Derivation stays the source of truth — this records
    what was derived, it does not replace it.
    """
    graph = await project_graph(session, group_id=group_id)
    rollup = await cascade_rollup(session, group_id=group_id)
    inserted = await persist_edges(session, group_id=group_id, graph=graph)
    snapshot = await write_snapshot(session, group_id=group_id, rollup=rollup, graph=graph)
    return graph, rollup, snapshot, inserted


def graph_payload(graph: CascadeGraph, rollup: CascadeRollup) -> dict[str, Any]:
    """The shape Stream D renders. Kept here so the API layer stays a pass-through."""
    return {
        "group_reference": graph.group_reference,
        "rule_version": graph.rule_version,
        "nodes": [node.model_dump(mode="json") for node in graph.nodes],
        "edges": [edge.model_dump(mode="json") for edge in graph.edges],
        "edge_counts_by_kind": _edge_counts(graph),
        "completeness": {
            "member_flight_count": graph.member_flight_count,
            "flights_with_evidence": graph.flights_with_evidence,
            "is_complete": graph.is_complete,
            "note": graph.completeness_note,
        },
        "source_action_ids": graph.source_action_ids,
        "source_prediction_ids": graph.source_prediction_ids,
        "snapshot_hash": snapshot_hash(rollup=rollup, graph=graph),
    }
