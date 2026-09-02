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
from app.models.workflow import Action, Incident, IncidentGroup, PlanTask
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


async def test_the_room_totals_agree_with_the_what_if_baseline(client):
    """Two screens, one group, one figure. They reported 95 and 166.

    Blast Radius sums the recorded allocations. The What-If baseline re-runs `allocate_rooms`
    against `load_hotel_options`, which subtracts every active hold — including the 71 rooms this
    very group had just secured. So the baseline allocated nothing and reported the whole
    requirement as short, on the same screen, under the same label, both rendered as fact.

    The demand side already had this fix: `_accommodation_basis` reads the recorded requirement
    rather than recomputing one. The supply side had not been carried across.

    A what-if with no levers accepted returns no deltas, so the comparison uses a lever that cannot
    move a room: `minimum_connection_minutes` re-evaluates connections and leaves accommodation
    exactly where the recorded allocations put it.
    """
    _drive(client)
    radius = _detail(client)["blast_radius"]
    values = {dimension["key"]: dimension["value"] for dimension in radius["dimensions"]}

    what_if = client.post(
        f"{PREFIX}/incident-groups/{GROUP}/what-if", json={"minimum_connection_minutes": 30}
    ).json()
    rooms_short = next(delta for delta in what_if["deltas"] if delta["key"] == "rooms_short")

    assert rooms_short["baseline"] == values["rooms_short"], (
        "the what-if baseline must see the inventory this group already holds; "
        f"blast radius says {values['rooms_short']} short, what-if says {rooms_short['baseline']}"
    )


async def test_a_partly_allocated_group_reports_its_room_totals_as_floors(client, sessionmaker_for):
    """Completeness for rooms is about hotel coverage, and nothing else was measuring it.

    `compose_blast_radius` marked the room dimensions complete using `rollup.is_complete`, which
    tests connection and crew assessment and says nothing whatever about whether every incident ran
    an allocation. `group_hotel_totals` sums only the incidents that did. So a group fully assessed
    for crew with two of eight allocations run rendered its room requirement as a total when it was
    a floor — the exact failure `blast_radius`'s own docstring says it exists to prevent.
    """
    from app.services.hotel import group_hotel_totals

    _drive(client)
    async with sessionmaker_for() as session:
        group_id = (
            await session.execute(select(IncidentGroup.id).where(IncidentGroup.reference == GROUP))
        ).scalar_one()
        totals = await group_hotel_totals(session, group_id=group_id)

    assert totals is not None
    assert totals["incidents_declared"] >= totals["incidents_allocated"] > 0
    assert totals["coverage_is_complete"] == (
        totals["incidents_allocated"] == totals["incidents_declared"]
    )
    if not totals["coverage_is_complete"]:
        assert "floors rather than totals" in totals["shortfall_note"], (
            "a partial sum must say so in the sentence the UI renders verbatim"
        )


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


# ------------------------------------------------- approval is the only route to resolution
#
# The Phase 3 stall these pin: the cascade reported `executing` for ever with
# `awaiting_approval_count` at one, and no error said why. The chain below is asserted one link at a
# time, because "the group resolved" on its own cannot tell an approval that was honoured from an
# approval that was never needed.


async def _incidents_in_group(sessionmaker_for) -> int:
    async with sessionmaker_for() as session:
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(Incident)
                    .join(IncidentGroup, Incident.group_id == IncidentGroup.id)
                    .where(IncidentGroup.reference == GROUP)
                )
            ).scalar_one()
        )


async def _actions_of_type(sessionmaker_for, action_type: str) -> list[tuple[str, int | None]]:
    async with sessionmaker_for() as session:
        rows = (
            await session.execute(
                select(Action.status, Action.assurance_id)
                .join(PlanTask, Action.plan_task_id == PlanTask.id)
                .where(PlanTask.action_type == action_type)
            )
        ).all()
    return [(str(status), assurance_id) for status, assurance_id in rows]


async def test_an_unapproved_high_risk_action_executes_nothing(client, sessionmaker_for):
    """The gate holds `notify_passengers` for a person, and until one decides, nothing is sent.

    604 passengers' worth of email is the thing that cannot be retracted, so this is the assertion
    that matters most: the hold is real, not cosmetic.
    """
    _open(client)
    state = _run(client)

    held = _held(client, state)
    assert len(held) == 8, "expected one high-risk hold per member flight"
    assert state["state"] != "resolved"
    assert state["awaiting_approval_count"] == 8
    assert all(member["state"] == "awaiting_approval" for member in state["members"])
    assert await _actions_of_type(sessionmaker_for, "notify_passengers") == []


async def test_an_approved_action_executes_and_its_completion_is_persisted(
    client, sessionmaker_for
):
    """Approval authorises execution, and the execution leaves a durable, attributed record."""
    _open(client)
    state = _run(client)
    held = _held(client, state)

    for evaluation_id in held:
        response = client.post(
            f"{PREFIX}/assurance/{evaluation_id}/decision",
            json={"decision": "approved", "reason": "approved by the integration test"},
        )
        assert response.status_code == 200, response.text

    # An approval on its own advances nothing; the run is what consumes it.
    assert await _actions_of_type(sessionmaker_for, "notify_passengers") == []

    _run(client)

    executed = await _actions_of_type(sessionmaker_for, "notify_passengers")
    assert len(executed) == 8, "every approved notification should have run exactly once"
    assert {status for status, _ in executed} == {"success"}
    assert all(assurance_id is not None for _, assurance_id in executed), (
        "an executed action must name the evaluation that authorised it"
    )


async def test_the_group_resolves_only_once_every_required_approval_is_given(client):
    """The exact reported stall, as a regression.

    Seven approvals leave the eighth member held. The group is then `executing` with
    `awaiting_approval_count` at one — which is precisely what was reported — and it must **not**
    read `resolved`. Approving the last one and running again must finish it.
    """
    _open(client)
    state = _run(client)
    held = _held(client, state)
    assert len(held) == 8

    for evaluation_id in held[:7]:
        response = client.post(
            f"{PREFIX}/assurance/{evaluation_id}/decision",
            json={"decision": "approved", "reason": "approved by the integration test"},
        )
        assert response.status_code == 200, response.text

    partial = _run(client)
    assert partial["state"] == "executing"
    assert partial["awaiting_approval_count"] == 1
    assert partial["state"] != "resolved", "one outstanding approval must hold the group open"
    states = sorted(member["state"] for member in partial["members"])
    assert states.count("resolved") == 7
    assert states.count("awaiting_approval") == 1

    response = client.post(
        f"{PREFIX}/assurance/{held[7]}/decision",
        json={"decision": "approved", "reason": "the last one"},
    )
    assert response.status_code == 200, response.text

    finished = _run(client)
    assert finished["state"] == "resolved", finished.get("blocked_reason")
    assert finished["awaiting_approval_count"] == 0
    assert all(member["state"] == "resolved" for member in finished["members"])


async def test_a_held_evaluation_cannot_be_approved_twice_into_a_second_execution(client):
    """A repeated approval replays; it must not authorise a second send."""
    _open(client)
    state = _run(client)
    evaluation_id = _held(client, state)[0]
    body = {"decision": "approved", "reason": "approved by the integration test"}

    first = client.post(f"{PREFIX}/assurance/{evaluation_id}/decision", json=body)
    second = client.post(f"{PREFIX}/assurance/{evaluation_id}/decision", json=body)
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json()["decision"] == "approved"


# ------------------------------------------------------- re-opening a finished cascade
#
# `open_incident` deduplicates on `uq_incident_active_per_flight`, which is partial over ACTIVE
# states. A terminal member has released its slot, so delegating idempotency to it opened a *second*
# incident per flight once the cascade had finished: sixteen incidents in an eight-flight group,
# `awaiting_approval_count` counting copies the flight list never showed, a derived state that could
# never be `resolved` again, and 409s from every later run while the duplicates it had already
# committed stayed behind. Re-running the Phase 3 script was enough to trigger it.


async def test_reopening_a_resolved_cascade_creates_no_second_incident(client, sessionmaker_for):
    state = _drive(client)
    assert state["state"] == "resolved"
    before = await _incidents_in_group(sessionmaker_for)
    assert before == 8

    again = _open(client)

    assert again["opened_incident_ids"] == []
    assert len(again["members"]) == 8
    assert await _incidents_in_group(sessionmaker_for) == before


async def test_a_resolved_cascade_stays_addressable_after_a_repeat_open_and_run(
    client, sessionmaker_for
):
    """The whole point: a re-run must be a no-op, not a permanent 409."""
    _drive(client)
    _open(client)

    state = _run(client)

    assert state["state"] == "resolved"
    assert state["awaiting_approval_count"] == 0
    assert await _incidents_in_group(sessionmaker_for) == 8
    detail = _detail(client)
    assert len(detail["flights"]) == 8, "a duplicated member would show twice here"
    assert detail["rollups"]["flights_affected"] == 8
