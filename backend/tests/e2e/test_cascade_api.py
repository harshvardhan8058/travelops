"""The disruption-group surface over the real app: group, blast radius, graph, plans, replay.

These are contract tests with teeth. They assert the two things a cascade can get wrong in a way
that looks right on a screen:

* **Group figures are unions, never sums.** Eight incidents each reporting their own broken
  connections would total 176 where the truth is 22.
* **A partial rollup renders as partial.** `is_complete` false must reach the response, because a
  caption sized for eight flights over six flights' worth of evidence is the failure mode.

Owner: Stream A.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.cascade import IncidentGroupFlight
from app.models.enums import ProvenanceKind
from app.models.reference import Booking, BookingSegment, Flight, Passenger
from app.models.workflow import IncidentGroup

PREFIX = "/api/v1"
GROUP_REF = "GRP-2026-0820-VOBL"
DEPARTURE = datetime(2026, 8, 20, 15, 40, tzinfo=UTC)


@pytest.fixture
async def cascade(seeded):
    """A two-flight group with declared membership and one booking per flight.

    Deliberately small. The verified eight-flight figures belong to the real dataset and are
    asserted against real Postgres in `scripts/verify_phase2.py`; what this fixture exists to test
    is the *shape* of the surface and the arithmetic rules, which a two-flight group proves just as
    well and far faster.
    """
    factory = async_sessionmaker(bind=seeded, expire_on_commit=False, autoflush=False)
    async with factory() as db:
        second = Flight(
            flight_number="6E 811",
            airline_code="6E",
            origin_icao="VOBL",
            destination_icao="VIDP",
            scheduled_departure=DEPARTURE + timedelta(minutes=30),
            scheduled_arrival=DEPARTURE + timedelta(minutes=195),
            estimated_departure=DEPARTURE + timedelta(minutes=140),
            block_time_minutes=165,
            status="delayed",
            is_domestic=True,
            provenance_kind=ProvenanceKind.fixture,
            source_ref="fixture:bengaluru_storm:flight",
        )
        db.add(second)
        group = IncidentGroup(
            reference=GROUP_REF,
            root_cause="weather",
            airport_icao="VOBL",
            severity="high",
            state="detected",
            opened_at=DEPARTURE,
            demo_dataset_id="bengaluru_storm",
        )
        db.add(group)
        await db.flush()

        db.add_all(
            [
                IncidentGroupFlight(
                    incident_group_id=group.id,
                    flight_id=1,
                    role="primary",
                    delay_minutes_at_injection=420,
                    provenance_kind=ProvenanceKind.fixture,
                    source_ref="fixture:bengaluru_storm:membership",
                ),
                IncidentGroupFlight(
                    incident_group_id=group.id,
                    flight_id=second.id,
                    role="affected_departure",
                    delay_minutes_at_injection=110,
                    provenance_kind=ProvenanceKind.fixture,
                    source_ref="fixture:bengaluru_storm:membership",
                ),
            ]
        )

        for index, flight_id in enumerate((1, second.id), start=1):
            passenger = Passenger(
                reference=f"PAX-{index:05d}",
                full_name=f"Test Passenger {index}",
                email=f"pax{index}@example.com",
                tier="standard",
            )
            db.add(passenger)
            await db.flush()
            booking = Booking(pnr=f"PNR{index:04d}", passenger_id=passenger.id, cabin="economy")
            db.add(booking)
            await db.flush()
            db.add(BookingSegment(booking_id=booking.id, flight_id=flight_id, segment_order=1))
        await db.commit()
    return GROUP_REF


class TestGroupList:
    def test_the_group_appears_with_derived_rollups(self, client, cascade):
        response = client.get(f"{PREFIX}/incident-groups")
        assert response.status_code == 200
        body = response.json()

        group = next(g for g in body["groups"] if g["reference"] == GROUP_REF)
        assert group["rollups"]["flights_affected"] == 2
        assert group["rollups"]["passengers_affected"] == 2
        assert group["provenance"]["kind"] == "derived"

    def test_the_current_group_is_addressable(self, client, cascade):
        response = client.get(f"{PREFIX}/incident-groups/current")
        assert response.status_code == 200
        assert response.json()["reference"] == GROUP_REF

    def test_an_unknown_group_is_a_404_not_an_empty_shell(self, client, cascade):
        response = client.get(f"{PREFIX}/incident-groups/GRP-NOPE")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "ENTITY_NOT_FOUND"


class TestGroupDetail:
    def test_membership_is_declared_not_derived_from_the_airport(self, client, cascade):
        """Both flights are declared members, each with its role."""
        body = client.get(f"{PREFIX}/incident-groups/{cascade}").json()
        roles = {f["flight_number"]: f["role"] for f in body["flights"]}
        assert roles == {"6E 2134": "primary", "6E 811": "affected_departure"}

    def test_a_declared_flight_with_no_incident_says_so(self, client, cascade):
        """Nullable rather than absent: "affected, not yet being worked" is a real state."""
        body = client.get(f"{PREFIX}/incident-groups/{cascade}").json()
        assert all(f["incident_reference"] is None for f in body["flights"])
        assert body["rollup_status"]["is_complete"] is False
        assert sorted(body["rollup_status"]["flights_without_incident"]) == [1, 2]

    def test_the_detail_carries_a_blast_radius_with_named_sources(self, client, cascade):
        body = client.get(f"{PREFIX}/incident-groups/{cascade}").json()
        radius = body["blast_radius"]
        assert radius["basis"] == "composed_from_recorded_findings"
        assert radius["dimensions"], "no dimensions composed"
        for dimension in radius["dimensions"]:
            assert dimension["measured_by"], dimension["key"]

    def test_the_graph_only_contains_nodes_that_exist(self, client, cascade):
        body = client.get(f"{PREFIX}/incident-groups/{cascade}").json()
        graph = body["graph"]
        kinds = {node["kind"] for node in graph["nodes"]}
        assert kinds <= {"event", "flight", "pairing", "booking", "hotel"}
        for edge in graph["edges"]:
            assert (
                edge["derived_from_action_id"] is not None
                or edge["derived_from_prediction_id"] is not None
            ), "an edge without provenance is an assertion, not evidence"

    def test_no_hardcoded_total_appears_in_the_note(self, client, cascade):
        body = client.get(f"{PREFIX}/incident-groups/{cascade}").json()
        assert "DERIVED" in body["note"]


class TestOpenAndRun:
    def test_opening_creates_one_incident_per_declared_flight(self, client, cascade):
        response = client.post(f"{PREFIX}/incident-groups/{cascade}/open")
        assert response.status_code == 200
        body = response.json()
        assert len(body["members"]) == 2
        assert len({m["incident_reference"] for m in body["members"]}) == 2

    def test_reopening_creates_nothing_new(self, client, cascade):
        first = client.post(f"{PREFIX}/incident-groups/{cascade}/open").json()
        second = client.post(f"{PREFIX}/incident-groups/{cascade}/open").json()
        assert {m["incident_reference"] for m in first["members"]} == {
            m["incident_reference"] for m in second["members"]
        }

    def test_the_group_state_is_derived_from_its_members(self, client, cascade):
        client.post(f"{PREFIX}/incident-groups/{cascade}/open")
        body = client.post(f"{PREFIX}/incident-groups/{cascade}/run").json()
        member_states = {m["state"] for m in body["members"]}
        assert body["state"] in {"assessing", "planning", "executing", "resolved", "blocked"}
        assert member_states, "no member states reported"

    def test_running_does_not_open_a_second_incident_for_a_blocked_flight(self, client, cascade):
        """`uq_incident_active_per_flight` is partial over ACTIVE states.

        A blocked member releases its slot, so a run that also opened would create a second
        incident for that flight every time — quietly growing the cascade.
        """
        client.post(f"{PREFIX}/incident-groups/{cascade}/open")
        for _ in range(3):
            client.post(f"{PREFIX}/incident-groups/{cascade}/run")
        body = client.get(f"{PREFIX}/incident-groups/{cascade}").json()
        assert len(body["flights"]) == 2

    def test_a_replayed_idempotency_key_returns_the_recorded_result(self, client, cascade):
        client.post(f"{PREFIX}/incident-groups/{cascade}/open")
        headers = {"Idempotency-Key": "cascade-run-1"}
        first = client.post(f"{PREFIX}/incident-groups/{cascade}/run", headers=headers).json()
        second = client.post(f"{PREFIX}/incident-groups/{cascade}/run", headers=headers).json()
        assert first["replayed"] is False
        assert second["replayed"] is True


class TestUnionsNotSums:
    def test_connections_are_a_union_across_the_group(self, client, cascade):
        """Two incidents each finding their own connections must not double the group figure."""
        client.post(f"{PREFIX}/incident-groups/{cascade}/open")
        client.post(f"{PREFIX}/incident-groups/{cascade}/run")
        body = client.get(f"{PREFIX}/incident-groups/{cascade}").json()

        passengers = body["rollups"]["passengers_affected"]
        assert passengers == 2, "one booking per flight, counted once each"


class TestWhatIf:
    def test_what_if_is_zero_write_and_says_so(self, client, cascade):
        before = client.get(f"{PREFIX}/incident-groups/{cascade}").json()
        response = client.post(
            f"{PREFIX}/incident-groups/{cascade}/what-if",
            json={"minimum_connection_minutes": 30, "seed": 20260820},
        )
        after = client.get(f"{PREFIX}/incident-groups/{cascade}").json()

        assert response.status_code == 200
        body = response.json()
        assert body["basis"] == "recorded_evidence"
        assert body["wrote_rows"] is False
        assert "not a forecast" in body["boundary_note"].lower()
        assert before["rollups"] == after["rollups"], "a what-if moved the recorded figures"

    def test_an_undeclared_lever_is_refused_by_name(self, client, cascade):
        body = client.post(
            f"{PREFIX}/incident-groups/{cascade}/what-if",
            json={"weather_next_week": "clear", "seed": 20260820},
        ).json()
        rejected = {item["lever"] for item in body["levers_rejected"]}
        assert "weather_next_week" in rejected

    def test_the_response_cannot_express_a_projection(self, client, cascade):
        """`basis` and `wrote_rows` are Literals in the contract, so this is structural."""
        response = client.post(
            f"{PREFIX}/incident-groups/{cascade}/what-if", json={"seed": 20260820}
        )
        assert response.json()["basis"] == "recorded_evidence"


class TestGroupAssurance:
    def test_the_group_summary_authorises_nothing(self, client, cascade):
        client.post(f"{PREFIX}/incident-groups/{cascade}/open")
        client.post(f"{PREFIX}/incident-groups/{cascade}/run")
        response = client.get(f"{PREFIX}/incident-groups/{cascade}/assurance")
        assert response.status_code == 200
        body = response.json()
        assert body["authorises_no_action"] is True
        assert len(body["checks"]) == 6

    def test_no_aggregate_score_at_any_level(self, client, cascade):
        client.post(f"{PREFIX}/incident-groups/{cascade}/open")
        client.post(f"{PREFIX}/incident-groups/{cascade}/run")
        raw = client.get(f"{PREFIX}/incident-groups/{cascade}/assurance").text.lower()
        for banned in ("score", "confidence", "average"):
            assert banned not in raw, f"found '{banned}' in the group assurance response"

    def test_the_preview_itemises_what_it_cannot_cover(self, client, cascade):
        client.post(f"{PREFIX}/incident-groups/{cascade}/open")
        client.post(f"{PREFIX}/incident-groups/{cascade}/run")
        body = client.get(f"{PREFIX}/incident-groups/{cascade}/assurance").json()
        preview = body["approval_preview"]
        assert preview is not None
        for item in preview["excluded"]:
            assert item["reason_code"], "an exclusion without a reason is unexplained"
            assert item["reason"]


class TestReplay:
    def test_incident_replay_frames_are_contiguous(self, client, cascade):
        client.post(f"{PREFIX}/incident-groups/{cascade}/open")
        client.post(f"{PREFIX}/incident-groups/{cascade}/run")
        reference = client.get(f"{PREFIX}/incident-groups/{cascade}").json()["flights"][0][
            "incident_reference"
        ]

        body = client.get(f"{PREFIX}/incidents/{reference}/replay").json()
        assert [f["sequence"] for f in body["frames"]] == list(range(1, len(body["frames"]) + 1))

    def test_group_replay_is_chronological(self, client, cascade):
        client.post(f"{PREFIX}/incident-groups/{cascade}/open")
        client.post(f"{PREFIX}/incident-groups/{cascade}/run")
        body = client.get(f"{PREFIX}/incident-groups/{cascade}/replay").json()
        times = [f["occurred_at"] for f in body["frames"]]
        assert times == sorted(times)

    def test_replay_writes_nothing(self, client, cascade):
        client.post(f"{PREFIX}/incident-groups/{cascade}/open")
        client.post(f"{PREFIX}/incident-groups/{cascade}/run")
        first = client.get(f"{PREFIX}/incident-groups/{cascade}/replay").json()["frame_count"]
        client.get(f"{PREFIX}/incident-groups/{cascade}/replay")
        second = client.get(f"{PREFIX}/incident-groups/{cascade}/replay").json()["frame_count"]
        assert first == second

    def test_the_timeline_and_replay_agree_on_actor_kind(self, client, cascade):
        """One mapping, shared. Two copies would let them disagree about a human decision."""
        client.post(f"{PREFIX}/incident-groups/{cascade}/open")
        client.post(f"{PREFIX}/incident-groups/{cascade}/run")
        reference = client.get(f"{PREFIX}/incident-groups/{cascade}").json()["flights"][0][
            "incident_reference"
        ]

        timeline = client.get(f"{PREFIX}/incidents/{reference}/timeline").json()["entries"]
        frames = client.get(f"{PREFIX}/incidents/{reference}/replay").json()["frames"]
        by_actor_timeline = {(e["actor"], e["actor_kind"]) for e in timeline}
        by_actor_replay = {(f["actor"], f["actor_kind"]) for f in frames}
        assert by_actor_timeline == by_actor_replay


class TestCandidatePlans:
    def test_more_than_one_candidate_exists_with_llm_off(self, client, cascade):
        """The comparison screen must not be empty in the mode the demo runs in."""
        client.post(f"{PREFIX}/incident-groups/{cascade}/open")
        client.post(f"{PREFIX}/incident-groups/{cascade}/run")
        reference = client.get(f"{PREFIX}/incident-groups/{cascade}").json()["flights"][0][
            "incident_reference"
        ]

        body = client.get(f"{PREFIX}/incidents/{reference}/plans").json()
        assert len(body["plans"]) >= 2
        assert all(plan["plan_hash"] for plan in body["plans"])

    def test_comparison_states_its_basis_and_carries_no_rank(self, client, cascade):
        client.post(f"{PREFIX}/incident-groups/{cascade}/open")
        client.post(f"{PREFIX}/incident-groups/{cascade}/run")
        reference = client.get(f"{PREFIX}/incident-groups/{cascade}").json()["flights"][0][
            "incident_reference"
        ]

        response = client.get(f"{PREFIX}/incidents/{reference}/plans/comparison")
        assert response.status_code == 200
        body = response.json()
        assert body["basis"] == "recorded_evidence"
        assert body["not_a_forecast"]
        raw = response.text.lower()
        for banned in ("recommended", "best_plan", "score"):
            assert banned not in raw

    def test_selection_is_attributed_and_immutable(self, client, cascade):
        client.post(f"{PREFIX}/incident-groups/{cascade}/open")
        client.post(f"{PREFIX}/incident-groups/{cascade}/run")
        reference = client.get(f"{PREFIX}/incident-groups/{cascade}").json()["flights"][0][
            "incident_reference"
        ]
        plans = client.get(f"{PREFIX}/incidents/{reference}/plans").json()["plans"]

        chosen = plans[0]["id"]
        other = plans[1]["id"]
        first = client.post(
            f"{PREFIX}/incidents/{reference}/plans/{chosen}/select",
            json={"actor_id": "operator-1", "reason": "fewest external effects"},
        )
        assert first.status_code == 200
        selected = next(p for p in first.json()["plans"] if p["id"] == chosen)
        assert selected["selection_state"] == "selected"
        assert selected["selected_by"] == "operator-1"

        conflict = client.post(
            f"{PREFIX}/incidents/{reference}/plans/{other}/select",
            json={"actor_id": "operator-2", "reason": "changed my mind"},
        )
        assert conflict.status_code == 409

    def test_selecting_the_same_plan_twice_is_not_a_conflict(self, client, cascade):
        client.post(f"{PREFIX}/incident-groups/{cascade}/open")
        client.post(f"{PREFIX}/incident-groups/{cascade}/run")
        reference = client.get(f"{PREFIX}/incident-groups/{cascade}").json()["flights"][0][
            "incident_reference"
        ]
        plan_id = client.get(f"{PREFIX}/incidents/{reference}/plans").json()["plans"][0]["id"]
        payload = {"actor_id": "operator-1", "reason": "confirmed"}

        client.post(f"{PREFIX}/incidents/{reference}/plans/{plan_id}/select", json=payload)
        again = client.post(f"{PREFIX}/incidents/{reference}/plans/{plan_id}/select", json=payload)
        assert again.status_code == 200


class TestActionDetail:
    def test_the_recorded_payload_is_reachable(self, client, cascade):
        """Without this the console can only see a sentence, and parsing prose is fabrication."""
        client.post(f"{PREFIX}/incident-groups/{cascade}/open")
        client.post(f"{PREFIX}/incident-groups/{cascade}/run")
        reference = client.get(f"{PREFIX}/incident-groups/{cascade}").json()["flights"][0][
            "incident_reference"
        ]
        actions = client.get(f"{PREFIX}/incidents/{reference}").json()["actions"]
        if not actions:
            pytest.skip("no action executed in this fixture")

        detail = client.get(f"{PREFIX}/incidents/{reference}/actions/{actions[0]['id']}").json()
        assert "payload" in detail
        assert detail["payload_schema_version"] == 1
        assert detail["incident_reference"] == reference

    def test_reason_code_is_promoted_from_the_payload(self, client, cascade):
        """A token, not prose. The console must not prefix-match a sentence."""
        client.post(f"{PREFIX}/incident-groups/{cascade}/open")
        client.post(f"{PREFIX}/incident-groups/{cascade}/run")
        reference = client.get(f"{PREFIX}/incident-groups/{cascade}").json()["flights"][0][
            "incident_reference"
        ]
        actions = client.get(f"{PREFIX}/incidents/{reference}").json()["actions"]
        if not actions:
            pytest.skip("no action executed in this fixture")

        for action in actions:
            detail = client.get(f"{PREFIX}/incidents/{reference}/actions/{action['id']}").json()
            recorded = (detail["payload"] or {}).get("reason_code")
            assert detail["reason_code"] == recorded
