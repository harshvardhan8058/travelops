"""The Executive Report must never narrate a resolved outcome the recorded state disagrees with.

Reported bug: the console was on `INC-2026-0903-VOBL-01`, whose replay ended in
`reserve_hotel_block -> BLOCKED / needs_human`, and the Executive Report nonetheless claimed all
174 passengers were re-accommodated and the incident ended without residual impact. The committed
fixture (`fixtures/reporter_report.v1_bengaluru_storm.json`) always narrates the happy path — every
fixture-mode request replays it unchanged — and `ReportGeneratorAgent.generate()` never told a live
call what the incident's actual state was either. Neither the fixture nor a live model call had any
way to know the reference on screen had not resolved.

The fix in `app/api/reasoning.py` re-derives `operational_state` from recorded rows on every call —
`Incident.state` directly for an incident, `derive_group_state(...)` over member states for a group,
never the persisted `incident_group.state` column, which is written only when `GroupOrchestrator`
syncs it and can lag a member that reached `blocked` on its own — and hard-overrides the response's
`status`/`summary`/`sections` whenever that state disagrees with `resolved`. This file drives the
real app over real Postgres to a genuinely blocked incident and a genuinely blocked group, the same
way the reported incident got there (a `reserve_hotel_block` shortfall followed by a rejected
high-risk approval), and asserts the report the endpoint actually returns.

Nothing here is UI-only: every assertion is against the JSON body `/reports/{id}` returns, not
against how a screen renders it.

Owner: Stream C.
"""

from __future__ import annotations

import pytest
from sqlalchemy import update

from app.db.seed import INCIDENT_GROUP_REFERENCE
from app.models.workflow import IncidentGroup
from tests.contract.postgres_support import requires_postgres

pytestmark = [pytest.mark.anyio, requires_postgres]

PREFIX = "/api/v1"
GROUP = INCIDENT_GROUP_REFERENCE


# --------------------------------------------------------------------------- journey driver


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


def _held(client, state: dict) -> list[dict]:
    """Every evaluation the gate holds across the group with no decision recorded yet.

    Returns the evaluation rows themselves (id, action_type, incident reference attached) rather
    than bare ids, so a caller can single out one member's `notify_passengers` hold without a
    second round trip.
    """
    rows: list[dict] = []
    for member in state["members"]:
        reference = member.get("incident_reference")
        if not reference:
            continue
        body = client.get(f"{PREFIX}/incidents/{reference}/assurance").json()
        for evaluation in body["evaluations"]:
            if evaluation["decision"] == "needs_human" and not evaluation.get("human_decision"):
                rows.append({**evaluation, "incident_reference": reference})
    return rows


def _drive_blocking_one_member(client, *, rounds: int = 12) -> tuple[dict, str]:
    """Drive the group to completion, except: the primary flight's `notify_passengers` hold is

    REJECTED rather than approved, once it appears. Every other hold is approved.

    This reproduces the reported incident's own shape rather than a synthetic one: the primary
    flight's `reserve_hotel_block` already comes back `needs_human` with a real room shortfall
    (see `test_real_group_journey.py::test_a_partial_hotel_allocation_does_not_strand_the_other_work`
    for that fact pinned independently). Continuing the plan and then rejecting the next high-risk
    hold — with nothing else left to do — is exactly the `_block(reason="operator rejected the
    remaining work")` path in `app/orchestrator/engine.py`, so the primary incident ends up
    `blocked` carrying a real `reserve_hotel_block` shortfall action, matching
    `reserve_hotel_block -> BLOCKED / needs_human` from the bug report.

    Returns the final group state and the primary flight's incident reference.
    """
    _open(client)
    detail = _detail(client)
    primary = next(flight for flight in detail["flights"] if flight["role"] == "primary")
    primary_reference = primary["incident_reference"]
    assert primary_reference, "the primary flight must have an incident open before this can work"

    state = _run(client)
    rejected_once = False
    for _ in range(rounds):
        held = _held(client, state)
        if not held:
            break
        for evaluation in held:
            reject_this_one = (
                not rejected_once
                and evaluation["incident_reference"] == primary_reference
                and evaluation["action_type"] == "notify_passengers"
            )
            decision = "rejected" if reject_this_one else "approved"
            response = client.post(
                f"{PREFIX}/assurance/{evaluation['id']}/decision",
                json={"decision": decision, "reason": "driven by the report-consistency test"},
            )
            assert response.status_code == 200, response.text
            if reject_this_one:
                rejected_once = True
        state = _run(client)
    assert rejected_once, "the primary flight's notify_passengers hold never appeared to reject"
    return state, primary_reference


# ------------------------------------------------------------- the reported bug, as a regression


class TestABlockedIncidentIsNeverNarratedAsResolved:
    async def test_the_primary_incident_actually_ends_up_blocked_with_a_hotel_shortfall(
        self, client
    ):
        """The premise. If this fails, the rest of the file is testing nothing."""
        state, primary_reference = _drive_blocking_one_member(client)
        assert state["state"] != "resolved", state.get("blocked_reason")

        incident = client.get(f"{PREFIX}/incidents/{primary_reference}").json()
        assert incident["state"] == "blocked"
        actions = {action["action_type"]: action for action in incident["actions"]}
        assert actions["reserve_hotel_block"]["status"] == "needs_human"
        assert "rooms short" in actions["reserve_hotel_block"]["reason"]

    async def test_the_incident_scoped_report_says_blocked_not_resolved(self, client):
        _, primary_reference = _drive_blocking_one_member(client)

        response = client.get(f"{PREFIX}/reports/{primary_reference}")
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["operational_state"] == "blocked"
        assert body["is_resolved"] is False
        assert body["narrative_overridden"] is True
        assert body["status"] != "success"

    async def test_the_narrative_makes_no_resolved_or_re_accommodated_claim(self, client):
        """The literal bug: 'all passengers were re-accommodated... ended without residual impact.'

        Checked against the returned summary and every section body, not just the summary, since
        the fixture's own 'Resolution' section is exactly where that claim lived.
        """
        _, primary_reference = _drive_blocking_one_member(client)
        body = client.get(f"{PREFIX}/reports/{primary_reference}").json()

        banned = ("re-accommodated", "ended without residual impact", "reached resolved state")
        text = body["summary"].lower() + " ".join(s["body"].lower() for s in body["sections"])
        for phrase in banned:
            assert phrase not in text, f"the corrected report still claims: {phrase!r}"
        assert not any(
            section["heading"].lower() in ("resolution",) for section in body["sections"]
        ), "a 'Resolution' section must not survive on an unresolved reference"
        assert "has not resolved" in body["summary"]

    async def test_the_correction_cites_the_real_recorded_shortfall(self, client):
        """The override must speak from evidence, not invent new prose."""
        _, primary_reference = _drive_blocking_one_member(client)
        body = client.get(f"{PREFIX}/reports/{primary_reference}").json()

        assert "reserve_hotel_block" in body["summary"]
        assert "rooms short" in body["summary"]

    async def test_the_first_section_is_current_status_not_a_hidden_resolution(self, client):
        _, primary_reference = _drive_blocking_one_member(client)
        body = client.get(f"{PREFIX}/reports/{primary_reference}").json()

        assert body["sections"][0]["heading"] == "Current status"
        assert "has not resolved" in body["sections"][0]["body"]


class TestTheGroupReportIsNeverStaleAgainstAResolvedColumn:
    async def test_the_group_report_says_blocked_while_one_member_is(self, client):
        state, _ = _drive_blocking_one_member(client)
        assert state["state"] == "blocked", state.get("blocked_reason")

        body = client.get(f"{PREFIX}/reports/{GROUP}").json()
        assert body["operational_state"] == "blocked"
        assert body["is_resolved"] is False
        assert body["narrative_overridden"] is True
        assert body["status"] != "success"
        assert "has not resolved" in body["summary"]

    async def test_a_stale_resolved_column_is_not_trusted(self, client, sessionmaker_for):
        """The design point the fix exists to hold: derive, never trust the stored column.

        `_sync_state` already keeps `incident_group.state` correct in the ordinary course of a
        run, so reaching a genuinely stale column requires writing around the orchestrator, the
        same way a lagging sync in production would leave a row nobody has re-derived since. If
        the endpoint reads that column instead of re-deriving it, this is the test that catches it.
        """
        state, _ = _drive_blocking_one_member(client)
        assert state["state"] == "blocked"

        async with sessionmaker_for() as session:
            await session.execute(
                update(IncidentGroup).where(IncidentGroup.reference == GROUP).values(state="resolved")
            )
            await session.commit()

        body = client.get(f"{PREFIX}/reports/{GROUP}").json()
        assert body["operational_state"] == "blocked", (
            "the endpoint read the stale persisted column instead of deriving from members"
        )
        assert body["is_resolved"] is False
        assert body["narrative_overridden"] is True


# ------------------------------------------------------------------ the happy path must survive
#
# The override must fire only when the state actually disagrees. A genuinely resolved incident or
# group must come back exactly as before: `is_resolved` true, nothing overridden, and the agent's
# own narrative — including a fixture's own honest disclosure of a shortfall — passed through
# unchanged.


def _resolved_group(client) -> dict:
    _open(client)
    state = _run(client)
    for _ in range(12):
        held = _held(client, state)
        if not held:
            break
        for evaluation in held:
            response = client.post(
                f"{PREFIX}/assurance/{evaluation['id']}/decision",
                json={"decision": "approved", "reason": "approved by the report-consistency test"},
            )
            assert response.status_code == 200, response.text
        state = _run(client)
    assert state["state"] == "resolved", state.get("blocked_reason")
    return state


class TestAResolvedReportIsUnmodified:
    async def test_the_group_report_is_not_overridden_when_genuinely_resolved(self, client):
        _resolved_group(client)

        body = client.get(f"{PREFIX}/reports/{GROUP}").json()
        assert body["operational_state"] == "resolved"
        assert body["is_resolved"] is True
        assert body["narrative_overridden"] is False
        assert body["status"] == "success"
        # The fixture's own honest disclosure of the room shortfall must still be there —
        # this fix must not paper over an existing, correctly-reported shortfall.
        assert any("shortfall" in section["body"].lower() for section in body["sections"])

    async def test_an_incident_scoped_report_is_not_overridden_when_its_incident_resolved(
        self, client
    ):
        state = _resolved_group(client)
        member_reference = state["members"][0]["incident_reference"]

        body = client.get(f"{PREFIX}/reports/{member_reference}").json()
        assert body["operational_state"] == "resolved"
        assert body["is_resolved"] is True
        assert body["narrative_overridden"] is False
        assert body["status"] == "success"
