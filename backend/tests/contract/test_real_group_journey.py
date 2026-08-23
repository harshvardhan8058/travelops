"""The whole Phase 2 journey through the real app, against real Postgres.

Bengaluru Storm -> 8-flight group -> cascade -> blast radius -> plan assurance -> approval ->
execution -> resolved -> what-if -> replay.

Nothing is stubbed: the real FastAPI app and routers, the real `GroupOrchestrator` driving the real
per-incident `Orchestrator`, the real gate reading the configured assurance config, the real service
registry from Stream A's single registration seam, and the seeded dataset in Postgres with the real
migrations applied.

This file exists because four green component suites were not evidence that the journey worked. Four
bugs only appeared once it ran end to end, and each was invisible from inside the component that
caused it — every one produced a plausible number rather than an error:

* the hotel search resolved the disruption airport from the first member flight's origin, which is
  the wrong airport for an arrival and non-deterministic besides, and reported "0 properties within
  the rate cap" — indistinguishable from every hotel being full;
* delay risk read the flight's origin weather, so UK 705 (VAAH to VOBL, storm at its destination)
  had no assessment and the graph drew seven root-cause edges for eight declared flights;
* the group blast radius carried no accommodation figures at all, so a disruption 232 rooms short
  reported nothing about rooms;
* a service reporting `needs_human` was treated as a failure, so a partial hotel allocation blocked
  the connection, crew and notification work for 604 passengers.

Each is pinned below by a test that fails if it comes back.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.db.seed import INCIDENT_GROUP_REFERENCE
from app.models.cascade import (
    CascadeSnapshot,
    DisruptionEdge,
    HotelInventoryHold,
    PassengerImpact,
)
from app.models.workflow import Action
from tests.contract.postgres_support import requires_postgres

pytestmark = [pytest.mark.anyio, requires_postgres]

PREFIX = "/api/v1"
GROUP = INCIDENT_GROUP_REFERENCE


def _open(client) -> dict:
    response = client.post(f"{PREFIX}/incident-groups/{GROUP}/open")
    assert response.status_code == 200, response.text
    return response.json()


def _run(client) -> dict:
    response = client.post(f"{PREFIX}/incident-groups/{GROUP}/run")
    assert response.status_code == 200, response.text
    return response.json()


def _detail(client) -> dict:
    response = client.get(f"{PREFIX}/incident-groups/{GROUP}")
    assert response.status_code == 200, response.text
    return response.json()


def _held(client, state: dict) -> list[int]:
    """Evaluations the gate held across the group with no decision recorded yet."""
    ids: list[int] = []
    for member in state["members"]:
        reference = member.get("incident_reference")
        if not reference:
            continue
        body = client.get(f"{PREFIX}/incidents/{reference}/assurance").json()
        ids += [
            evaluation["id"]
            for evaluation in body["evaluations"]
            if evaluation["decision"] == "needs_human" and not evaluation.get("human_decision")
        ]
    return ids


def _drive(client, *, rounds: int = 12) -> dict:
    """Advance the group, approving whatever the gate holds, until nothing is held.

    A loop, because the gate does not reveal what it will hold until the earlier tasks are assured.
    """
    _open(client)
    state = _run(client)
    for _ in range(rounds):
        held = _held(client, state)
        if not held:
            break
        for evaluation_id in held:
            response = client.post(
                f"{PREFIX}/assurance/{evaluation_id}/decision",
                json={"decision": "approved", "reason": "approved by the integration test"},
            )
            assert response.status_code == 200, response.text
        state = _run(client)
    return state


# ------------------------------------------------------------------ before any run


async def test_a_seeded_group_declares_its_flights_before_anything_runs(client):
    """Declared membership is true the moment the dataset lands, and says it is incomplete.

    Two things must be visible at once: eight flights and 604 passengers are known, and nothing
    has been assessed. A rollup reporting zero flights here would make a seeded cascade
    indistinguishable from an empty one.
    """
    body = client.get(f"{PREFIX}/incident-groups").json()
    group = next(item for item in body["groups"] if item["reference"] == GROUP)

    assert group["rollups"]["flights_affected"] == 8
    assert group["rollups"]["passengers_affected"] == 604
    assert group["rollups"]["connections_at_risk"] == 0
    assert group["rollups"]["crew_pairings_affected"] == 0

    status = group["rollup_status"]
    assert status["membership_is_declared"] is True
    assert status["is_complete"] is False
    assert sorted(status["flights_without_incident"]) == [1, 2, 3, 5, 6, 7, 8, 9]


# ------------------------------------------------------------------------ the run


async def test_opening_the_group_creates_one_incident_per_declared_flight(client):
    """Eight incidents, not one group incident.

    The per-flight incident stays the unit of authorisation. A single group-level incident would
    collapse eight operational decisions into one click and lose which flight each action was
    authorised for.
    """
    state = _open(client)
    assert len(state["members"]) == 8
    assert len(state["opened_incident_ids"]) == 8
    roles = [member["role"] for member in state["members"]]
    assert roles.count("primary") == 1
    assert roles.count("affected_arrival") == 1
    assert roles.count("affected_departure") == 6


async def test_opening_twice_opens_nothing_new(client):
    _open(client)
    again = _open(client)
    assert again["opened_incident_ids"] == []
    assert len(again["members"]) == 8


async def test_the_group_reaches_resolved_through_the_approvals_it_asks_for(client):
    state = _drive(client)
    assert state["state"] == "resolved", state.get("blocked_reason")
    assert state["awaiting_approval_count"] == 0
    assert all(member["state"] == "resolved" for member in state["members"])


async def test_a_partial_hotel_allocation_does_not_strand_the_other_work(client):
    """The regression that matters most for the journey.

    `reserve_hotel_block` secures 71 of the 87 rooms the primary flight needs and reports
    `needs_human` with a named shortfall. Treating that as a failure blocked the incident,
    abandoning the rooms already held *and* stopping the connection, crew and notification work for
    604 passengers because 32 of them lacked a bed.

    A `failure`, a skip, or a refusal with no provenance still blocks — that distinction is what
    keeps an unimplemented service from being quietly filed as an outstanding item.
    """
    _drive(client)
    detail = _detail(client)
    primary = next(flight for flight in detail["flights"] if flight["role"] == "primary")
    incident = client.get(f"{PREFIX}/incidents/{primary['incident_reference']}").json()

    actions = {action["action_type"]: action for action in incident["actions"]}
    assert "reserve_hotel_block" in actions
    assert actions["reserve_hotel_block"]["status"] == "needs_human"
    assert "rooms short" in actions["reserve_hotel_block"]["reason"]
    # And the rest of the plan still ran, and the incident still resolved.
    assert actions["notify_passengers"]["status"] == "success"
    assert actions["assess_crew_impact"]["status"] == "success"
    assert incident["state"] == "resolved"


# ------------------------------------------------------------- the derived cascade


async def test_the_worked_group_reports_the_verified_figures(client):
    """8 / 604 / 22 / 11 / 9, every one derived from recorded rows."""
    _drive(client)
    detail = _detail(client)

    assert detail["rollups"]["flights_affected"] == 8
    assert detail["rollups"]["passengers_affected"] == 604
    assert detail["rollups"]["connections_at_risk"] == 22
    assert detail["rollups"]["candidate_hotels"] == 11
    assert detail["rollups"]["crew_pairings_affected"] == 9
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
    """Two renderings of one set of facts.

    A picture that disagrees with the headline beside it ends a demo.
    """
    _drive(client)
    detail = _detail(client)
    graph = detail["graph"]
    rollups = detail["rollups"]

    kinds: dict[str, int] = {}
    for node in graph["nodes"]:
        kinds[node["kind"]] = kinds.get(node["kind"], 0) + 1

    assert kinds["event"] == 1
    assert kinds["flight"] == rollups["flights_affected"]
    assert kinds["pairing"] == rollups["crew_pairings_affected"]
    assert kinds["booking"] == rollups["connections_at_risk"]
    assert graph["completeness"]["is_complete"] is True


async def test_one_root_cause_edge_per_declared_flight(client):
    """Eight, including the arrival.

    Delay risk is assessed against the group's airport, so UK 705 — VAAH to VOBL — is explained by
    the storm at its destination. Reading its origin's weather left it with no assessment and the
    graph with seven edges for eight flights: a flight in the picture with nothing explaining it.
    """
    _drive(client)
    graph = _detail(client)["graph"]
    assert graph["edge_counts_by_kind"]["root_cause"] == 8


async def test_every_edge_names_the_recorded_row_it_came_from(client):
    _drive(client)
    graph = _detail(client)["graph"]
    assert graph["edges"]
    for edge in graph["edges"]:
        by_action = edge["derived_from_action_id"] is not None
        by_prediction = edge["derived_from_prediction_id"] is not None
        # Exactly one, which the CHECK constraint on `disruption_edge` also enforces.
        assert by_action != by_prediction, edge
        # Root cause comes from a prediction: the weather is not an action anyone took.
        if edge["edge_kind"] == "root_cause":
            assert by_prediction
        else:
            assert by_action


async def test_no_edge_dangles(client):
    """A dangling edge draws a floating box with no explanation attached to it."""
    _drive(client)
    graph = _detail(client)["graph"]
    refs = {node["ref"] for node in graph["nodes"]}
    for edge in graph["edges"]:
        assert edge["source_ref"] in refs
        assert edge["target_ref"] in refs


async def test_accommodation_edges_exist_for_a_partial_allocation(client):
    """71 rooms were committed, so the cascade has to show them.

    The allocation is recorded `needs_human`. Reading only `success` actions drew a cascade with no
    accommodation edge while those rooms sat held in the ledger — the graph missing a relationship
    the database can prove.
    """
    _drive(client)
    graph = _detail(client)["graph"]
    assert graph["edge_counts_by_kind"].get("accommodation", 0) > 0
    assert any(node["kind"] == "hotel" for node in graph["nodes"])


# ------------------------------------------------------------------- blast radius


async def test_the_blast_radius_repeats_the_rollup_and_states_completeness(client):
    _drive(client)
    radius = _detail(client)["blast_radius"]
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


async def test_the_blast_radius_carries_the_group_room_shortfall(client):
    """The figures an operator most needs when inventory does not cover the disruption.

    They were absent entirely: the API composed the blast radius without accommodation figures,
    so a cascade 232 rooms short reported nothing about rooms. The totals are summed in Stream C's
    service rather than in the API, because summing an action payload in the transport layer is how
    22 distinct at-risk bookings become 176.
    """
    _drive(client)
    radius = _detail(client)["blast_radius"]
    values = {dimension["key"]: dimension["value"] for dimension in radius["dimensions"]}

    assert values["rooms_required"] > 71, "the group needs more rooms than the cap can supply"
    assert values["rooms_short"] > 0
    assert values["rooms_required"] > values["rooms_short"]
    assert values["committed_cost_inr"] > 0


async def test_display_strings_stay_ascii(client):
    """No U+2192 or U+20B9 in a rendered string.

    'Inter' and 'JetBrains Mono' are webfonts. On a machine without them the fallback draws both
    as a tofu box, and a box where an arrow should be reads as a rendering fault that undermines
    every figure beside it.
    """
    _drive(client)
    body = client.get(f"{PREFIX}/incident-groups/{GROUP}").text
    assert "\u2192" not in body
    assert "\u20b9" not in body


# --------------------------------------------------------------- per-passenger impact


async def test_impacts_are_empty_and_say_so_before_a_run(client):
    """Nothing recorded yet is reported as nothing recorded, not as zeros.

    Zero passengers with zero cohorts reads, on a wall display, as "every passenger is fine". The
    unassessed factors are still named here, because they are a property of the ruleset rather than
    of any run.
    """
    body = client.get(f"{PREFIX}/incident-groups/{GROUP}/impacts").json()

    assert body["passengers_assessed"] == 0
    assert body["cohorts"] == []
    assert body["computed_at"] is None
    assert "No passenger priorities are recorded" in body["note"]
    assert [item["factor"] for item in body["unassessed_factors"]]


async def test_the_run_records_a_priority_for_every_affected_passenger(client):
    """604 passengers ranked, each with the factors that produced the index.

    The count comes from the same rows the rollup counts, so a priority list that disagreed with
    the headline would fail here rather than be discovered on a projector.
    """
    _drive(client)
    rollup = _detail(client)["rollups"]
    body = client.get(f"{PREFIX}/incident-groups/{GROUP}/impacts?limit=1000").json()

    assert body["passengers_assessed"] == rollup["passengers_affected"]
    assert body["returned"] == body["passengers_assessed"]
    assert body["basis"] == "persisted_records"
    assert body["computed_at"] is not None
    assert body["ruleset_hash"]

    banded = sum(cohort["passenger_count"] for cohort in body["cohorts"])
    assert banded == body["passengers_assessed"], "every passenger sits in exactly one band"

    for passenger in body["passengers"]:
        assert passenger["pnr"], "an id is not a person; the PNR must be present"
        attributed = sum(factor["weight"] for factor in passenger["factors"])
        assert min(100, attributed) == passenger["priority_index"], (
            "every point of the index must be attributable to a named factor"
        )


async def test_the_ranking_reflects_the_recorded_connection_findings(client):
    """`broken_connection` is read from the persisted Connection action, not recomputed.

    One service owns one fact. If the ranking recomputed it, the priority list and the 22-connection
    headline could disagree and nothing would say which was right.
    """
    _drive(client)
    body = client.get(f"{PREFIX}/incident-groups/{GROUP}/impacts?limit=1000").json()
    connections = _detail(client)["rollups"]["connections_at_risk"]

    carrying = [
        passenger
        for passenger in body["passengers"]
        if any(factor["factor"] == "broken_connection" for factor in passenger["factors"])
    ]
    assert len(carrying) == connections, (
        "the passengers carrying a broken connection are exactly the at-risk connections"
    )
    for passenger in carrying:
        source = next(
            factor["source"]
            for factor in passenger["factors"]
            if factor["factor"] == "broken_connection"
        )
        assert source == "connection_broken"


async def test_unestablished_factors_are_named_rather_than_reported_false(client):
    """The distinction the surface turns on.

    `overnight_exposure` and `journey_incomplete` are Rebooking's findings and nothing establishes
    them, so they are false in every row. Left unqualified that renders as "nobody needs
    rebooking", when the truth is that nobody has looked. Both must be named, and neither may
    appear as a scored factor on any passenger.
    """
    _drive(client)
    body = client.get(f"{PREFIX}/incident-groups/{GROUP}/impacts?limit=1000").json()

    named = {item["factor"] for item in body["unassessed_factors"]}
    assert named == {"overnight_exposure", "journey_incomplete"}
    for item in body["unassessed_factors"]:
        assert item["established_by"]
        assert item["reason"]

    scored = {
        factor["factor"] for passenger in body["passengers"] for factor in passenger["factors"]
    }
    assert not (scored & named), "a factor nothing established must never be scored"


async def test_impacts_are_replaced_rather_than_appended(client, sessionmaker_for):
    """Re-running leaves one current row per passenger, never two that disagree.

    The surface that would decide who gets one of a short supply of rooms cannot be ambiguous about
    a passenger's band. History belongs in `decision_log` and `cascade_snapshot`.
    """
    _drive(client)
    first = client.get(f"{PREFIX}/incident-groups/{GROUP}/impacts").json()
    _run(client)
    second = client.get(f"{PREFIX}/incident-groups/{GROUP}/impacts").json()

    assert second["passengers_assessed"] == first["passengers_assessed"]

    async with sessionmaker_for() as session:
        rows = (
            await session.execute(
                select(func.count()).select_from(PassengerImpact),
            )
        ).scalar_one()
        distinct = (
            await session.execute(
                select(func.count(func.distinct(PassengerImpact.passenger_id))).select_from(
                    PassengerImpact
                ),
            )
        ).scalar_one()
    assert rows == distinct == second["passengers_assessed"]


async def test_the_impact_run_is_journalled_and_authorises_nothing(client):
    """A ranking that decides an ordering must be visible in the audit trail as a ranking."""
    _drive(client)
    frames = client.get(f"{PREFIX}/incident-groups/{GROUP}/replay").json()["frames"]
    recorded = [frame for frame in frames if frame["event_type"] == "PASSENGER_IMPACT_RECORDED"]

    assert recorded, "the ranking must appear in the group journal"
    assert "PASSENGER_IMPACT_FAILED" not in {frame["event_type"] for frame in frames}
    detail = recorded[-1]["detail"]
    assert detail["basis"] == "persisted_records"
    assert detail["authorises_no_action"] is True
    assert detail["rows_written"] == detail["passengers_assessed"]
    assert sorted(detail["unassessed_factors"]) == ["journey_incomplete", "overnight_exposure"]
    assert recorded[-1]["human_decision_id"] is None, "a ranking is not a decision"


# ------------------------------------------------------------------ plan assurance


async def test_plan_assurance_is_group_scoped_and_authorises_nothing(client):
    """P2-D1, and the boundary is in the payload rather than in a comment."""
    _open(client)
    _run(client)
    body = client.get(f"{PREFIX}/incident-groups/{GROUP}/assurance").json()

    assert body["group_reference"] == GROUP
    assert body["authorises_no_action"] is True
    assert body["plan_hash"]
    assert body["incidents"]
    # A single request, not a client-side fan-out over eight incidents: a fan-out lets a partial
    # failure read as a pass.
    assert len(body["incidents"]) >= 1


async def test_group_exposure_reports_the_rooms_and_money_actually_committed(
    client, sessionmaker_for
):
    """The gate must measure real exposure, not a permanent "unknown".

    `_recorded_exposure` filtered on `Action.status == "succeeded"` — a `TaskState` value, not an
    `ActionStatus` member — so the query matched no rows on any run and both figures came back
    `None` forever. Because Stream B treats an unknown figure as a breach rather than as zero, the
    symptom was a group whose rooms and money were fully recorded in the ledger reporting an
    unknown-exposure breach. That reads as caution, which is why nothing caught it.

    Pinned against the hold ledger rather than against a constant: `rooms_committed` is a
    commitment, and the earlier code read `rooms_required`, which is demand. Those differ by exactly
    the shortfall this scenario exists to show.
    """
    _drive(client)
    exposure = client.get(f"{PREFIX}/incident-groups/{GROUP}/assurance").json()["exposure"]

    assert exposure["rooms_committed"] is not None, "rooms were committed; the gate must see them"
    assert exposure["total_exposure_inr"] is not None
    assert exposure["total_exposure_inr"] > 0

    async with sessionmaker_for() as session:
        held = int(
            (
                await session.execute(select(func.coalesce(func.sum(HotelInventoryHold.rooms), 0)))
            ).scalar_one()
        )
    assert exposure["rooms_committed"] == held, (
        "committed rooms must equal the rooms held in the ledger, not the rooms required"
    )

    radius = {
        dimension["key"]: dimension["value"]
        for dimension in _detail(client)["blast_radius"]["dimensions"]
    }
    assert exposure["rooms_committed"] < radius["rooms_required"], (
        "the scenario is short of rooms, so a commitment below the requirement is the point"
    )
    assert exposure["total_exposure_inr"] == radius["committed_cost_inr"], (
        "one service owns the accommodation totals; the gate and the blast radius must agree"
    )


async def test_a_plan_approval_never_covers_high_risk(client):
    """P2-D3. `notify_passengers` is high risk and always needs its own decision.

    Asserted through the preview rather than by reading the tables, because the preview is what an
    operator sees before clicking. An approval that turns out to cover less than they assumed is
    the failure this surface exists to prevent.
    """
    _open(client)
    _run(client)
    body = client.get(f"{PREFIX}/incident-groups/{GROUP}/assurance").json()
    preview = body.get("approval_preview")
    if preview is None:
        pytest.skip("no plan approval is available on this run, so there is nothing to preview")

    covered = {item["action_type"] for item in preview["covered"]}
    excluded = {item["action_type"] for item in preview["excluded"]}
    assert "notify_passengers" not in covered
    if "notify_passengers" in excluded:
        reason = next(
            item["reason"]
            for item in preview["excluded"]
            if item["action_type"] == "notify_passengers"
        )
        assert "high" in reason.lower() or "own decision" in reason.lower()


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
        json={"minimum_connection_minutes": 20, "max_rate_inr": 20000, "not_a_lever": 1},
    ).json()
    after = await census()

    assert after == before
    assert body["wrote_rows"] is False
    assert body["basis"] == "recorded_evidence"
    assert [item["lever"] for item in body["levers_rejected"]] == ["not_a_lever"]


async def test_what_if_is_deterministic(client):
    _drive(client)
    payload = {"minimum_connection_minutes": 30, "max_rate_inr": 9000}
    first = client.post(f"{PREFIX}/incident-groups/{GROUP}/what-if", json=dict(payload)).json()
    second = client.post(f"{PREFIX}/incident-groups/{GROUP}/what-if", json=dict(payload)).json()
    assert first["deltas"] == second["deltas"]


# ------------------------------------------------------------------------- replay


async def test_replay_is_an_ordered_read_only_fold(client):
    _drive(client)
    body = client.get(f"{PREFIX}/incident-groups/{GROUP}/replay").json()

    assert body["is_read_only"] is True
    assert body["frame_count"] > 50
    frames = body["frames"]
    assert [frame["sequence"] for frame in frames] == sorted(frame["sequence"] for frame in frames)
    assert all(
        frames[i]["occurred_at"] <= frames[i + 1]["occurred_at"] for i in range(len(frames) - 1)
    )
    # Actor identity is separate from status, at group scope as well as per incident.
    kinds = {frame["actor_kind"] for frame in frames}
    assert "human" in kinds


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

    async def edges() -> int:
        async with sessionmaker_for() as session:
            result = await session.execute(select(func.count()).select_from(DisruptionEdge))
            return int(result.scalar_one())

    first = await edges()
    _run(client)
    assert await edges() == first
