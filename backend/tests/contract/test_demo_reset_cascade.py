"""`demo-reset --cascade` after a full cascade run, against real Postgres.

This is the engine-specific half of a test that already exists. `test_bengaluru_storm.py`'s
`test_demo_reset_leaves_no_orphaned_workflow_rows` drives `_demo_reset` twice on SQLite and
counts orphans, and its docstring says why that is not enough: SQLite does not enforce foreign
keys by default, so a reset that Postgres rejects outright leaves dangling rows there and
passes. It promises the Postgres half is "verified separately" — this file is that.

The bug it pins: `_clear_workflow_records` deleted `action` while `disruption_edge`
`derived_from_action_id` still pointed at those rows, so `demo-reset --cascade` on the demo
machine died with `fk_disruption_edge_derived_from_action_id_action`. The cascade projection's
output — edges, snapshots, passenger impact, hotel holds — is derived from a run rather than
seeded, so neither the CLI's cleanup nor `reset_demo_dataset` carried it.

It escaped every suite for one reason: no test called the production cleanup path against
Postgres. `tests/contract/conftest.py`'s own `clear_workflow` deletes those four tables before
`Action` in exactly the right order, so every Postgres contract test reset itself correctly
while the CLI the demo actually runs did not.

Both `--cascade` and the single-incident form are driven twice, because the second call is the
one that fails: the first run of a fresh database has no projected rows to trip over.

Owner: Stream C.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.db.seed import DEMO_DATASET_ID, INCIDENT_GROUP_REFERENCE
from app.models.cascade import (
    CascadeSnapshot,
    DisruptionEdge,
    HotelInventoryHold,
    PassengerImpact,
)
from app.models.workflow import Action, Incident
from tests.contract.postgres_support import requires_postgres

pytestmark = [pytest.mark.anyio, requires_postgres]

PREFIX = "/api/v1"
GROUP = INCIDENT_GROUP_REFERENCE


def _drive_the_cascade(client, *, rounds: int = 12) -> dict:
    """Open the group and advance it, approving whatever the gate holds, until nothing is held.

    The same loop as `test_real_group_journey`. It has to run to completion here: the crew,
    connection and accommodation edges that carry `derived_from_action_id` only appear once the
    actions behind them have executed, and those are the rows the bug was about.
    """
    assert client.post(f"{PREFIX}/incident-groups/{GROUP}/open").status_code == 200
    state = client.post(f"{PREFIX}/incident-groups/{GROUP}/run").json()
    for _ in range(rounds):
        held: list[int] = []
        for member in state["members"]:
            reference = member.get("incident_reference")
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
                json={"decision": "approved", "reason": "approved by the reset regression test"},
            )
            assert response.status_code == 200, response.text
        state = client.post(f"{PREFIX}/incident-groups/{GROUP}/run").json()
    return state


async def _count(sessionmaker_for, model, column=None) -> int:
    async with sessionmaker_for() as session:
        target = func.count() if column is None else func.count(column)
        return int((await session.execute(select(target).select_from(model))).scalar_one())


async def _projection_counts(sessionmaker_for) -> dict[str, int]:
    return {
        "disruption_edge": await _count(sessionmaker_for, DisruptionEdge),
        "edges_by_action": await _count(
            sessionmaker_for, DisruptionEdge, DisruptionEdge.derived_from_action_id
        ),
        "cascade_snapshot": await _count(sessionmaker_for, CascadeSnapshot),
        "passenger_impact": await _count(sessionmaker_for, PassengerImpact),
        "hotel_inventory_hold": await _count(sessionmaker_for, HotelInventoryHold),
        "action": await _count(sessionmaker_for, Action),
    }


async def _demo_incident_count(sessionmaker_for) -> int:
    async with sessionmaker_for() as session:
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(Incident)
                    .where(Incident.demo_dataset_id == DEMO_DATASET_ID)
                )
            ).scalar_one()
        )


async def _reset(sessionmaker_for, *, cascade: bool) -> None:
    """One `demo-reset` in its own committed transaction, exactly as the CLI runs it."""
    from app.cli import _demo_reset

    async with sessionmaker_for() as session:
        await _demo_reset(session, "bengaluru_storm", cascade=cascade)
        await session.commit()


# --------------------------------------------------------------- the projection is really there


async def test_a_full_cascade_run_leaves_action_derived_edges_behind(client, sessionmaker_for):
    """Guards the two tests below from passing vacuously.

    If the journey stopped producing edges that name an `action`, the reset tests would still
    go green while covering nothing — the reported foreign key would simply never be loaded.
    """
    state = _drive_the_cascade(client)
    assert state["state"] == "resolved", state.get("blocked_reason")

    counts = await _projection_counts(sessionmaker_for)
    assert counts["edges_by_action"] > 0, (
        "no disruption_edge row names an action, so this file no longer covers "
        "fk_disruption_edge_derived_from_action_id_action"
    )
    assert counts["action"] > 0
    # The other three tables are what then hold `incident_group` against the seed's own delete.
    assert counts["cascade_snapshot"] > 0
    assert counts["passenger_impact"] > 0
    assert counts["hotel_inventory_hold"] > 0


# ------------------------------------------------------------------------ repeated demo-reset


async def test_demo_reset_cascade_survives_a_full_cascade_run_twice(client, sessionmaker_for):
    """The reported failure: `demo-reset --cascade` on a database that has a projected cascade.

    Asserted by letting the foreign key violation propagate rather than by counting orphans.
    On Postgres a wrong order raises `ForeignKeyViolationError`, so the exception *is* the
    finding; counting would only restate what the engine already refused to do.
    """
    _drive_the_cascade(client)
    before = await _projection_counts(sessionmaker_for)
    assert before["edges_by_action"] > 0

    await _reset(sessionmaker_for, cascade=True)
    await _reset(sessionmaker_for, cascade=True)

    # Re-injected, not merely deleted: `--cascade` opens one incident per declared member.
    assert await _demo_incident_count(sessionmaker_for) == 8

    after = await _projection_counts(sessionmaker_for)
    assert after["disruption_edge"] == 0
    assert after["cascade_snapshot"] == 0
    assert after["passenger_impact"] == 0
    assert after["hotel_inventory_hold"] == 0
    assert after["action"] == 0


async def test_demo_reset_without_cascade_also_survives_a_cascade_run(client, sessionmaker_for):
    """The single-incident form clears the same projected rows.

    Both CLI forms share `_clear_workflow_records`, so a fix that only worked for `--cascade`
    would mean `make demo-reset` still fell over on a database an earlier cascade had touched.
    """
    _drive_the_cascade(client)
    assert (await _projection_counts(sessionmaker_for))["edges_by_action"] > 0

    await _reset(sessionmaker_for, cascade=False)
    await _reset(sessionmaker_for, cascade=False)

    assert await _demo_incident_count(sessionmaker_for) == 1
    assert (await _projection_counts(sessionmaker_for))["disruption_edge"] == 0


# ----------------------------------------------------------------------------- clean database


async def test_demo_reset_cascade_is_repeatable_with_nothing_to_clear(client, sessionmaker_for):
    """A reset on a freshly seeded database, twice, with no run in between.

    The path where every id list is empty. `in_([])` is valid SQL but the guard clauses around
    it are easy to get wrong, and this is the form an operator hits first.
    """
    await _reset(sessionmaker_for, cascade=True)
    await _reset(sessionmaker_for, cascade=True)

    assert await _demo_incident_count(sessionmaker_for) == 8
    assert (await _projection_counts(sessionmaker_for))["disruption_edge"] == 0
