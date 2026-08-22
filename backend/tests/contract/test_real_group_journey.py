"""The whole Phase 2 journey through the real app, against real Postgres.

Bengaluru Storm -> 8-flight group -> cascade -> blast radius -> plan assurance -> approval ->
execution -> resolved -> what-if -> replay.

Nothing is stubbed: the real FastAPI app and routers, the real `GroupOrchestrator` driving the real
per-incident `Orchestrator`, the real gate reading the configured assurance config, the real service
registry from Stream A's single registration seam, and the seeded dataset in Postgres with the real
migrations applied.

This exists because the individually-green pieces were not evidence that the journey worked. Four
bugs only appeared once it ran end to end, and each was invisible from inside the component that
caused it:

* the hotel search resolved the disruption airport by counting origin and destination appearances,
  tied on a single flight, and searched Delhi — reporting "0 properties within the rate cap", which
  is indistinguishable from every hotel being full;
* delay risk read the origin's weather, so the one inbound flight in the cascade had no assessment
  and the graph drew seven root-cause edges for eight declared flights;
* the blast radius took the newest hotel action's figures as the group's, showing "9 rooms required"
  for a disruption needing 303 against 71 available;
* a service reporting `needs_human` was treated as a failure, so a partial hotel allocation blocked
  the connection, crew and notification work for 604 passengers.

Owner: Stream A.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.db.seed import INCIDENT_GROUP_REFERENCE
from app.models.cascade import CascadeSnapshot, DisruptionEdge, HotelInventoryHold
from app.models.workflow import Action
from tests.contract.postgres_support import requires_postgres

pytestmark = [pytest.mark.anyio, requires_postgres]

PREFIX = "/api/v1"
GROUP = INCIDENT_GROUP_REFERENCE


def _run(client) -> dict:
    response = client.post(f"{PREFIX}/incident-groups/{GROUP}/run")
    assert response.status_code == 200, response.text
    return response.json()


def _drive(client, *, rounds: int = 12) -> dict:
    """Advance the group, approving everything the gate holds, until nothing is held.

    A loop, because the gate does not reveal what it will hold until the earlier tasks are assured.
    """
    state = _run(client)
    for _ in range(rounds):
        held: list[int] = []
        for incident in state["incidents"]:
            reference = incident["incident_reference"]
            if not reference:
                continue
            body = client.get(f"{PREFIX}/incidents/{reference}/assurance").json()
            held += [
                evaluation["id"]
                for evaluation in body["evaluations"]
                if evaluation["decision"] == "needs_human" and not evaluation.get("human_decision")
            ]
        if not held:
            break
        for evaluation_id in held:
            response = client.post(
                f"{PREFIX}/assurance/{evaluation_id}/decision",
                json={"decision": "approved", "reason": "approved by the integration test"},
            )
            assert response.status_code == 200, response.text
        state = _run(client)
        if state["is_terminal"]:
            break
    return state


# ------------------------------------------------------------------ before any run


async def test_a_seeded_group_declares_its_flights_before_anything_runs(client):
    """Declared membership is true the moment the dataset lands, and says it is incomplete.

    The two things that must be simultaneously visible: eight flights and 604 passengers are known,
    and nothing has been assessed. A rollup that reported zero flights here would make a seeded
    cascade indistinguishable from an empty one.
    """
    body = client.get(f"{PREFIX}/incident-groups").json()
    group = next(item for item in body["groups"] if item["reference"] == GROUP)

    assert group["rollups"]["flights_affected"] == 8
    assert group["rollups"]["passengers_affected"] == 604
    assert group["rollups"]["candidate_hotels"] == 11
    assert group["rollups"]["connections_at_risk"] == 0
    assert group["rollups"]["crew_pairings_affected"] == 0

    status = group["rollup_status"]
    assert status["membership_is_declared"] is True
    assert status["member_flight_ids"] == [1, 2, 3, 5, 6, 7, 8, 9]
    assert status["is_complete"] is False
    assert sorted(status["flights_without_incident"]) == [1, 2, 3, 5, 6, 7, 8, 9]
    assert "partial" in status["note"]


# ------------------------------------------------------------------------ the run


async def test_the_group_run_opens_one_incident_per_declared_flight(client):
    """Eight incidents, not one group incident.

    The per-flight incident stays the unit of authorisation. A single group-level incident would
    collapse eight operational decisions into one click and lose which flight each action was
    authorised for.
    """
    state = _run(client)
    assert len(state["incidents"]) == 8
    assert sorted({item["role"] for item in state["incidents"]}) == [
        "affected_arrival",
        "affected_departure",
        "primary",
    ]
    assert [item["role"] for item in state["incidents"]].count("primary") == 1
    assert [item["role"] for item in state["incidents"]].count("affected_arrival") == 1
    assert all(item["incident_reference"] for item in state["incidents"])


async def test_every_incident_gets_its_own_plan_with_its_own_hash(client):
    """A plan hash per incident, all distinct. An approval is bound to one plan, not to a group."""
    state = _run(client)
    hashes = [item["plan_hash"] for item in state["incidents"]]
    assert all(hashes)
    assert len(set(hashes)) == 8


async def test_the_group_is_not_terminal_while_any_member_waits(client):
    """`all`, not `any`. Seven resolved and one waiting is not a finished recovery."""
    state = _run(client)
    assert state["is_terminal"] is False
    assert state["states"].get("awaiting_approval") == 8
    assert "waiting for an operator decision" in state["note"]


async def test_the_group_reaches_resolved_through_the_approvals_it_asks_for(client):
    state = _drive(client)
    assert state["is_terminal"] is True
    assert state["states"] == {"resolved": 8}


# ------------------------------------------------------------- the derived cascade


async def test_the_worked_group_reports_the_verified_figures(client):
    """8 / 604 / 22 / 11 / 9, every one derived from recorded rows."""
    _drive(client)
    detail = client.get(f"{PREFIX}/incident-groups/{GROUP}").json()

    assert detail["rollups"] == {
        "flights_affected": 8,
        "passengers_affected": 604,
        "connections_at_risk": 22,
        "candidate_hotels": 11,
        "crew_pairings_affected": 9,
    }
    assert detail["rollup_status"]["is_complete"] is True
    assert detail["rollup_status"]["flights_without_incident"] == []
    assert len(detail["flights"]) == 8
    assert len(detail["crew_pairings"]) == 9
    assert {pairing["mechanism"] for pairing in detail["crew_pairings"]} == {
        "operating",
        "onward_duty",
        "second_pairing",
        "positioning",
    }


async def test_the_graph_agrees_with_the_rollup_it_sits_beside(client):
    """The assertion that matters most on screen: two renderings of one set of facts."""
    _drive(client)
    detail = client.get(f"{PREFIX}/incident-groups/{GROUP}").json()
    graph = detail["graph"]
    rollups = detail["rollups"]

    kinds: dict[str, int] = {}
    for node in graph["nodes"]:
        kinds[node["kind"]] = kinds.get(node["kind"], 0) + 1

    assert kinds["flight"] == rollups["flights_affected"]
    assert kinds["pairing"] == rollups["crew_pairings_affected"]
    assert kinds["booking"] == rollups["connections_at_risk"]
    assert kinds["event"] == 1
    assert graph["completeness"]["is_complete"] is True


async def test_one_root_cause_edge_per_declared_flight(client):
    """Eight, including the arrival.

    Delay risk is assessed against the group's airport, so UK 705 — VAAH to VOBL — is explained by
    the storm at its destination. Reading its origin's weather left it with no assessment and the
    graph with seven edges for eight flights: a flight in the picture with nothing explaining it.
    """
    _drive(client)
    graph = client.get(f"{PREFIX}/incident-groups/{GROUP}").json()["graph"]
    assert graph["edge_counts_by_kind"]["root_cause"] == 8


async def test_every_edge_names_the_recorded_row_it_came_from(client):
    _drive(client)
    graph = client.get(f"{PREFIX}/incident-groups/{GROUP}").json()["graph"]
    assert graph["edges"]
    for edge in graph["edges"]:
        kind, _, identifier = edge["derived_from"].partition(":")
        assert kind in {"action", "prediction"}, edge
        assert identifier.isdigit(), edge
        # Root cause comes from a prediction: the weather is not an action anyone took.
        if edge["edge_kind"] == "root_cause":
            assert kind == "prediction"
        else:
            assert kind == "action"


async def test_no_edge_dangles(client):
    """A dangling edge draws a floating box with no explanation attached to it."""
    _drive(client)
    graph = client.get(f"{PREFIX}/incident-groups/{GROUP}").json()["graph"]
    refs = {node["ref"] for node in graph["nodes"]}
    for edge in graph["edges"]:
        assert edge["source_ref"] in refs
        assert edge["target_ref"] in refs


async def test_accommodation_edges_exist_for_a_partial_allocation(client):
    """71 rooms were committed, so the cascade has to show them.

    The allocation is recorded `needs_human` because it is 16 rooms short of the primary flight's
    requirement. Reading only `success` actions drew a cascade with no accommodation edges while
    those rooms sat held in the ledger — the graph missing a relationship the database can prove.
    """
    _drive(client)
    graph = client.get(f"{PREFIX}/incident-groups/{GROUP}").json()["graph"]
    assert graph["edge_counts_by_kind"].get("accommodation", 0) > 0
    assert any(node["kind"] == "hotel" for node in graph["nodes"])


# ------------------------------------------------------------------- blast radius


async def test_the_blast_radius_repeats_the_rollup_and_states_completeness(client):
    _drive(client)
    detail = client.get(f"{PREFIX}/incident-groups/{GROUP}").json()
    radius = detail["blast_radius"]
    values = {dimension["key"]: dimension["value"] for dimension in radius["dimensions"]}

    assert radius["basis"] == "composed_from_recorded_findings"
    assert radius["completeness"]["ratio"] == "8/8"
    assert values["flights"] == 8
    assert values["passengers"] == 604
    assert values["connections"] == 22
    assert values["crew_pairings"] == 9
    assert values["candidate_hotels"] == 11
    for dimension in radius["dimensions"]:
        assert dimension["measured_by"]
        assert "confidence" not in dimension
        assert "probability" not in dimension


async def test_the_room_shortfall_is_the_group_total_not_the_last_action(client):
    """303 required against 71 available.

    Eight flights draw on one finite inventory, so the last allocation to run sees only what is
    left. Reporting its figures as the group's showed "9 rooms required, 0 short" for a disruption
    that is 232 rooms short — the shortfall is the whole point of the scenario.
    """
    _drive(client)
    radius = client.get(f"{PREFIX}/incident-groups/{GROUP}").json()["blast_radius"]
    values = {dimension["key"]: dimension["value"] for dimension in radius["dimensions"]}
    assert values["rooms_required"] > values.get("rooms_short", 0) > 0
    assert values["rooms_required"] > 71


async def test_display_strings_stay_ascii(client):
    """No U+2192 or U+20B9 anywhere in a rendered string.

    'Inter' and 'JetBrains Mono' are webfonts. On a machine without them the fallback draws both as
    a tofu box, and a box where an arrow should be reads as a rendering fault that undermines every
    figure beside it.
    """
    _drive(client)
    body = client.get(f"{PREFIX}/incident-groups/{GROUP}").text
    assert "\u2192" not in body
    assert "\u20b9" not in body


# ------------------------------------------------------------------ plan assurance


async def test_plan_assurance_is_group_scoped_and_authorises_nothing(client):
    """P2-D1. One evaluation per member plan, each scoped to this disruption."""
    _run(client)
    body = client.get(f"{PREFIX}/incident-groups/{GROUP}/plan-assurance").json()

    assert body["group_reference"] == GROUP
    assert body["config_version"] == "assurance-v2"
    assert len(body["plans"]) == 8
    for plan in body["plans"]:
        assert plan["group_reference"] == GROUP
        assert plan["authorises_no_action"] is True
        assert plan["plan_hash"]
        assert len(plan["checks"]) == 6
        assert plan["incident_reference"]


async def test_an_inadmissible_plan_cannot_be_approved(client):
    """A decision cannot cure failed evidence, and the refusal names which checks.

    Before the group is worked, coverage is incomplete and the exposure is unknown — Stream B treats
    an unknown exposure as a breach rather than a zero — so no plan is approvable yet. The refusal
    is a 409 naming the blocking checks, not a generic rejection.
    """
    _run(client)
    body = client.get(f"{PREFIX}/incident-groups/{GROUP}/plan-assurance").json()
    blocked = next(plan for plan in body["plans"] if plan["blocking"])

    response = client.post(
        f"{PREFIX}/incident-groups/{GROUP}/plans/{blocked['plan_id']}/approval",
        json={"reason": "trying to approve a blocked plan"},
    )
    assert response.status_code == 409, response.text
    message = response.json()["error"]["message"]
    assert "cannot be approved" in message


# ------------------------------------------------------------------------ what-if


async def test_what_if_writes_nothing(client, sessionmaker_for):
    """P2-D2, proved by counting every row in every table before and after."""
    from app.db.base import Base

    _drive(client)

    async def census() -> dict[str, int]:
        async with sessionmaker_for() as session:
            return {
                name: int(
                    (await session.execute(select(func.count()).select_from(table))).scalar_one()
                )
                for name, table in sorted(Base.metadata.tables.items())
            }

    before = await census()
    body = client.post(
        f"{PREFIX}/incident-groups/{GROUP}/what-if",
        json={
            "levers": {
                "minimum_connection_minutes": 20,
                "max_rate_inr": 20000,
                "passengers_per_room": 3,
                "not_a_lever": 1,
            }
        },
    ).json()
    after = await census()

    assert after == before
    assert body["wrote_rows"] is False
    assert body["basis"] == "recorded_evidence"
    assert [item["lever"] for item in body["levers_rejected"]] == ["not_a_lever"]
    assert "Not a simulation" in body["boundary_note"]


async def test_raising_the_rate_cap_reduces_the_shortfall(client):
    """A real re-evaluation, not a stub: the same allocator over a substituted cap."""
    _drive(client)
    body = client.post(
        f"{PREFIX}/incident-groups/{GROUP}/what-if",
        json={"levers": {"max_rate_inr": 20000}},
    ).json()
    shortfall = next(delta for delta in body["deltas"] if delta["key"] == "rooms_short")
    assert shortfall["baseline"] > 0
    assert shortfall["scenario"] < shortfall["baseline"]


async def test_what_if_is_deterministic(client):
    _drive(client)
    payload = {"levers": {"minimum_connection_minutes": 30, "max_rate_inr": 9000}}
    first = client.post(f"{PREFIX}/incident-groups/{GROUP}/what-if", json=payload).json()
    second = client.post(f"{PREFIX}/incident-groups/{GROUP}/what-if", json=payload).json()
    assert first["deltas"] == second["deltas"]


# ------------------------------------------------------------------------- replay


async def test_replay_is_an_ordered_fold_over_immutable_records(client):
    _drive(client)
    body = client.get(f"{PREFIX}/incident-groups/{GROUP}/replay").json()

    assert body["frame_count"] > 50
    frames = body["frames"]
    assert [frame["index"] for frame in frames] == list(range(len(frames)))
    assert all(
        frames[i]["occurred_at"] <= frames[i + 1]["occurred_at"] for i in range(len(frames) - 1)
    )
    # Actor identity is separate from status, at group scope as well as per incident.
    kinds = {frame["actor_kind"] for frame in frames}
    assert "human" in kinds
    assert "system" in kinds


async def test_the_group_events_are_on_the_record(client):
    _drive(client)
    frames = client.get(f"{PREFIX}/incident-groups/{GROUP}/replay").json()["frames"]
    events = {frame["event_type"] for frame in frames}
    for event in ("GROUP_INCIDENTS_OPENED", "GROUP_RUN_COMPLETED", "GROUP_SNAPSHOT_RECORDED"):
        assert event in events
    # Group entries carry the group scope in their detail, because `decision_log` has one
    # scope column and a NULL incident would hide the entry from every timeline view.
    group_frame = next(frame for frame in frames if frame["event_type"] == "GROUP_RUN_COMPLETED")
    assert group_frame["detail"]["group_reference"] == GROUP


async def test_an_outstanding_item_is_named_rather_than_hidden(client):
    """The hotel shortfall must be readable on the timeline, not only inferable from a metric."""
    _drive(client)
    frames = client.get(f"{PREFIX}/incident-groups/{GROUP}/replay").json()["frames"]
    outstanding = [frame for frame in frames if frame["event_type"] == "TASK_NEEDS_HUMAN"]
    assert outstanding
    assert any("reserve_hotel_block" in frame["summary"] for frame in outstanding)
    resolved = [
        frame
        for frame in frames
        if frame["event_type"] == "STATE_CHANGED" and "still needing a person" in frame["summary"]
    ]
    assert resolved, "the resolve summary must name the outstanding work"


# -------------------------------------------------------------- persisted evidence


async def test_the_run_leaves_the_evidence_it_claims(client, sessionmaker_for):
    _drive(client)
    async with sessionmaker_for() as session:

        async def count(model) -> int:
            return int(
                (await session.execute(select(func.count()).select_from(model))).scalar_one()
            )

        assert await count(DisruptionEdge) > 0
        assert await count(CascadeSnapshot) > 0
        assert await count(HotelInventoryHold) > 0
        rooms = int(
            (
                await session.execute(select(func.coalesce(func.sum(HotelInventoryHold.rooms), 0)))
            ).scalar_one()
        )
        assert rooms == 71, "the six properties inside the rate cap hold exactly 71 rooms"

        unauthorised = int(
            (
                await session.execute(
                    select(func.count()).select_from(Action).where(Action.assurance_id.is_(None))
                )
            ).scalar_one()
        )
        assert unauthorised == 0, "no action may exist without an authorising evaluation"


async def test_reprojecting_does_not_multiply_the_graph(client, sessionmaker_for):
    """A dashboard that reprojects on every poll must not grow the edge table."""
    _drive(client)
    async with sessionmaker_for() as session:
        first = int(
            (await session.execute(select(func.count()).select_from(DisruptionEdge))).scalar_one()
        )
    _run(client)
    async with sessionmaker_for() as session:
        second = int(
            (await session.execute(select(func.count()).select_from(DisruptionEdge))).scalar_one()
        )
    assert second == first
