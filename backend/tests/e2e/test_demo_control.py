"""The demo control surface, and the promises it makes — Phase 5.

A demo used to need a terminal. These routes remove that, which means they are the first surface an
evaluator touches — so what they must never do is more important than what they do:

  * a simulation may not invent a delay, weather, or a passenger count;
  * a definition the dataset cannot support must say so rather than disappear;
  * a destructive control must refuse a mis-click and refuse a production database;
  * the catalogue must be reproducible, or "run the demo twice and compare" is meaningless.

The load-bearing test in this file is `TestASimulationFeedsTheRealLifecycle`: it takes what
`GET /demo/simulations` publishes and POSTs it to `/scenarios` unmodified. If the catalogue ever
starts composing its own operational facts, the scenario contract rejects it and that test fails —
which is the only way to keep "there is no second lifecycle" true rather than merely stated.

Owner: Stream A.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.enums import ProvenanceKind
from app.models.reference import Booking, BookingSegment, Flight, Passenger
from app.schemas.demo import RESET_CONFIRMATION

PREFIX = "/api/v1"
DEPARTURE = datetime(2026, 8, 20, 15, 40, tzinfo=UTC)


def _factory(engine):
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@pytest.fixture
async def board(seeded):
    """A small VOBL departure board with recorded delays, plus one connecting itinerary.

    Built through the real models so the catalogue resolves against rows rather than literals. The
    delays differ on purpose: the catalogue promises the most delayed flight leads, and a board
    where every delay matched could not tell a correct ordering from an accidental one.
    """
    async with _factory(seeded)() as db:
        second = Flight(
            flight_number="6E 811",
            airline_code="6E",
            origin_icao="VOBL",
            destination_icao="VIDP",
            scheduled_departure=DEPARTURE + timedelta(minutes=30),
            scheduled_arrival=DEPARTURE + timedelta(minutes=195),
            # 110 minutes late: less than 6E 2134's 420, so it must not lead.
            estimated_departure=DEPARTURE + timedelta(minutes=140),
            block_time_minutes=165,
            status="delayed",
            provenance_kind=ProvenanceKind.fixture,
            source_ref="test:second",
        )
        onward = Flight(
            flight_number="AI 101",
            airline_code="AI",
            origin_icao="VIDP",
            destination_icao="VOBL",
            scheduled_departure=DEPARTURE + timedelta(minutes=300),
            scheduled_arrival=DEPARTURE + timedelta(minutes=465),
            estimated_departure=None,
            block_time_minutes=165,
            status="scheduled",
            provenance_kind=ProvenanceKind.fixture,
            source_ref="test:onward",
        )
        db.add_all([second, onward])
        await db.flush()

        passenger = Passenger(
            reference="PAX-00001",
            full_name="Ada Lovelace",
            email="pax-00001@example.com",
            tier="gold",
            provenance_kind=ProvenanceKind.synthetic,
        )
        db.add(passenger)
        await db.flush()
        booking = Booking(pnr="QT7HJ2", passenger_id=passenger.id, cabin="economy")
        db.add(booking)
        await db.flush()
        # Flight 1 (6E 2134) inbound, AI 101 onward: this is what makes a connection recorded.
        db.add_all(
            [
                BookingSegment(booking_id=booking.id, flight_id=1, segment_order=1),
                BookingSegment(booking_id=booking.id, flight_id=onward.id, segment_order=2),
            ]
        )
        await db.commit()
        return {"second_flight_id": second.id, "onward_flight_id": onward.id}


def _dataset(client):
    return client.get(f"{PREFIX}/demo/dataset").json()


def _simulations(client):
    return client.get(f"{PREFIX}/demo/simulations").json()


def _by_id(body, identifier: str):
    return next(entry for entry in body["simulations"] if entry["id"] == identifier)


# ---------------------------------------------------------------- dataset


class TestTheDatasetSurfaceReadsTheDatabase:
    def test_counts_are_read_back_rather_than_assumed(self, client, board):
        body = _dataset(client)

        # Three flights exist: the seeded 6E 2134 plus the two this fixture added.
        assert body["flights"] == 3
        assert body["airports"] == 2
        assert body["bookings"] == 1
        assert body["booking_segments"] == 2

    def test_the_table_list_matches_the_headline_figures(self, client, board):
        body = _dataset(client)
        rows = {entry["table"]: entry["rows"] for entry in body["tables"]}

        assert rows["flight"] == body["flights"]
        assert rows["booking_segment"] == body["booking_segments"]

    def test_is_seeded_is_derived_from_the_tables_not_stored(self, client, board):
        assert _dataset(client)["is_seeded"] is True

    def test_a_dataset_without_bookings_is_not_reported_as_seeded(self, client, incident):
        """The bare `seeded` fixture has flights but no passengers, so a demo cannot run on it."""
        body = _dataset(client)

        assert body["bookings"] == 0
        assert body["is_seeded"] is False

    def test_no_cascade_is_current_until_an_incident_exists(self, client, board):
        body = _dataset(client)

        assert body["incidents"] == 0
        assert body["current_group_reference"] is None

    def test_an_opened_incident_is_counted(self, client, incident):
        body = _dataset(client)

        assert body["incidents"] == 1

    def test_reset_is_allowed_in_a_development_environment(self, client, board):
        body = _dataset(client)

        assert body["reset_allowed"] is True
        assert body["app_env"] in {"development", "demo", "test"}

    def test_the_note_says_the_counts_are_live(self, client, board):
        assert "not cached" in _dataset(client)["note"]


# ---------------------------------------------------------------- catalogue


class TestTheCatalogueIsResolvedAgainstRealRows:
    def test_all_three_simulations_are_published(self, client, board):
        body = _simulations(client)

        assert [entry["id"] for entry in body["simulations"]] == [
            "bengaluru_severe_weather",
            "airport_cancellation_cascade",
            "connection_risk",
        ]
        assert body["catalogue_version"] == "simulation-catalogue-v1"
        assert body["basis"] == "recorded_dataset_selection"

    def test_members_carry_the_recorded_delay_not_a_declared_one(self, client, board):
        """420 and 110 are what the flight rows say. Nothing here may round or invent them."""
        weather = _by_id(_simulations(client), "bengaluru_severe_weather")
        delays = {member["flight_number"]: member["delay_minutes"] for member in weather["members"]}

        assert delays == {"6E 2134": 420, "6E 811": 110}

    def test_the_most_delayed_flight_leads(self, client, board):
        weather = _by_id(_simulations(client), "bengaluru_severe_weather")

        assert weather["members"][0]["flight_number"] == "6E 2134"
        assert weather["members"][0]["role"] == "primary"

    def test_exactly_one_primary_is_declared(self, client, board):
        for entry in _simulations(client)["simulations"]:
            if not entry["runnable"]:
                continue
            primaries = [m for m in entry["members"] if m["role"] == "primary"]
            assert len(primaries) == 1, entry["id"]

    def test_only_departures_from_the_named_airport_are_selected(self, client, board):
        """AI 101 departs VIDP, so it cannot appear in a VOBL cascade."""
        for entry in _simulations(client)["simulations"]:
            for member in entry["members"]:
                assert member["origin_icao"] == entry["airport_icao"]

    def test_passengers_are_counted_from_bookings(self, client, board):
        weather = _by_id(_simulations(client), "bengaluru_severe_weather")

        assert weather["passengers_affected"] == 1

    def test_an_uncountable_figure_is_null_rather_than_zero(self, client, seeded):
        """No bookings are seeded here. Claiming 0 passengers would be a fabricated total.

        Deliberately on `seeded` rather than `incident`: the `incident` fixture opens a workflow on
        flight 1, which now correctly blocks this definition — and a blocked definition reports a
        null passenger count for a different reason, so the test would have passed while proving
        nothing. `runnable` is asserted to keep it that way.
        """
        weather = _by_id(_simulations(client), "bengaluru_severe_weather")

        assert weather["runnable"] is True, weather["blocked_reason"]
        assert weather["members"], "a runnable definition must declare flights"
        assert weather["passengers_affected"] is None

    def test_every_definition_declares_simulated_provenance(self, client, board):
        for entry in _simulations(client)["simulations"]:
            assert entry["provenance"]["kind"] == "simulated"
            assert entry["provenance"]["provider"] == "demo.simulation_catalogue"

    def test_the_note_states_that_delays_are_recorded(self, client, board):
        note = _simulations(client)["note"].lower()

        assert "recorded" in note
        assert "cannot invent" in note

    def test_a_flight_already_in_an_active_workflow_blocks_the_simulation(self, client, incident):
        """`runnable` must mean "can be started now", not "the dataset has suitable rows".

        `POST /scenarios/{ref}/start` refuses a member flight owned by another active workflow with
        409 INVALID_STATE_TRANSITION. Before this was checked here the console offered an enabled
        button whose only possible outcome was that 409 — the browser proof caught it opening a
        scenario group and then failing to open any incident.

        The `incident` fixture opens a workflow on flight 1, the primary of this definition.
        """
        weather = _by_id(_simulations(client), "bengaluru_severe_weather")

        assert weather["runnable"] is False
        assert weather["blocked_reason"] is not None
        # Names the incident that owns the flight, so the reason is actionable rather than abstract.
        assert "INC-2026-0820-VOBL-01" in weather["blocked_reason"]
        # And says how to get out of the state, since both routes out are controls this surface has.
        assert "reset the demo data" in weather["blocked_reason"].lower()

    def test_a_blocked_simulation_is_still_listed_with_its_reason(self, client, incident):
        """Hiding it would leave an operator wondering where the simulation went."""
        body = _simulations(client)
        ids = [entry["id"] for entry in body["simulations"]]

        assert "bengaluru_severe_weather" in ids
        assert body["runnable_count"] == sum(
            1 for entry in body["simulations"] if entry["runnable"]
        )

    def test_an_unaffected_definition_stays_runnable_alongside_a_blocked_one(
        self, client, incident
    ):
        """The conflict is per-flight, so it must not blanket-block the catalogue.

        `connection_risk` requires an onward connection that this fixture does not record, so it is
        blocked for its own reason rather than the conflict — which is exactly the point: each entry
        reports the obstacle that actually applies to it.
        """
        reasons = {
            entry["id"]: entry["blocked_reason"] for entry in _simulations(client)["simulations"]
        }

        assert "active workflow" in (reasons["bengaluru_severe_weather"] or "")
        assert "active workflow" not in (reasons["connection_risk"] or "")

    def test_the_selection_is_reproducible(self, client, board):
        """Run the demo twice and compare is only meaningful if the selection is stable."""
        first = _simulations(client)
        second = _simulations(client)

        assert first == second


class TestAnUnsupportedDefinitionSaysSoRatherThanVanishing:
    def test_the_connection_simulation_is_blocked_without_a_connecting_itinerary(
        self, client, incident
    ):
        connection = _by_id(_simulations(client), "connection_risk")

        assert connection["runnable"] is False
        assert connection["members"] == []
        assert "onward segment" in connection["blocked_reason"]

    def test_a_blocked_definition_is_still_listed(self, client, incident):
        body = _simulations(client)

        assert len(body["simulations"]) == 3
        assert body["runnable_count"] < 3

    def test_the_connection_simulation_runs_once_a_connection_is_recorded(self, client, board):
        connection = _by_id(_simulations(client), "connection_risk")

        assert connection["runnable"] is True
        assert connection["blocked_reason"] is None
        assert [m["flight_number"] for m in connection["members"]] == ["6E 2134"]

    async def test_a_board_with_no_recorded_delay_is_refused(self, client, seeded):
        """A cascade in which nothing is late is not a disruption, so it is not offered."""
        import sqlalchemy

        async with _factory(seeded)() as db:
            flight = (await db.execute(sqlalchemy.select(Flight))).scalars().one()
            flight.estimated_departure = None
            await db.commit()

        weather = _by_id(_simulations(client), "bengaluru_severe_weather")

        assert weather["runnable"] is False
        assert "no departure from this airport has a recorded delay" in weather["blocked_reason"]

    def test_runnable_count_matches_the_runnable_entries(self, client, board):
        body = _simulations(client)

        assert body["runnable_count"] == sum(1 for e in body["simulations"] if e["runnable"])


# ---------------------------------------------------------------- the integration that matters


class TestASimulationFeedsTheRealLifecycle:
    """The guarantee: a published simulation is accepted by the EXISTING scenario contract.

    `POST /scenarios` refuses a declared delay that disagrees with the recorded one, refuses a
    member whose airport does not match, and refuses anything other than exactly one primary. So if
    this passes, the catalogue provably composed no operational facts of its own — and if somebody
    later teaches it to, this fails rather than a second lifecycle quietly appearing.
    """

    def test_a_published_simulation_is_accepted_unmodified(self, client, board):
        simulation = _by_id(_simulations(client), "bengaluru_severe_weather")

        response = client.post(
            f"{PREFIX}/scenarios",
            json={
                "root_cause": simulation["root_cause"],
                "airport_icao": simulation["airport_icao"],
                "severity": simulation["severity"],
                "effective_at": "2026-08-20T15:36:00Z",
                "actor_id": "operator-1",
                # Passed through exactly as published. Nothing is recomputed here.
                "members": [
                    {
                        "flight_id": member["flight_id"],
                        "role": member["role"],
                        "delay_minutes": member["delay_minutes"],
                    }
                    for member in simulation["members"]
                ],
            },
        )

        assert response.status_code in {200, 201}, response.json()
        body = response.json()
        assert body["scenario_reference"].startswith("SCN-")
        assert body["provenance"]["kind"] == "simulated"

    def test_a_tampered_delay_is_refused_by_the_scenario_contract(self, client, board):
        """Proves the guard above is real rather than incidental."""
        simulation = _by_id(_simulations(client), "bengaluru_severe_weather")
        members = [
            {
                "flight_id": member["flight_id"],
                "role": member["role"],
                # One minute more than the dataset records.
                "delay_minutes": member["delay_minutes"] + 1,
            }
            for member in simulation["members"]
        ]

        response = client.post(
            f"{PREFIX}/scenarios",
            json={
                "root_cause": simulation["root_cause"],
                "airport_icao": simulation["airport_icao"],
                "severity": simulation["severity"],
                "effective_at": "2026-08-20T15:36:00Z",
                "actor_id": "operator-1",
                "members": members,
            },
        )

        assert response.status_code >= 400


# ---------------------------------------------------------------- reset


class TestResetIsGatedTwice:
    def test_a_wrong_confirmation_phrase_is_refused(self, client, board):
        response = client.post(
            f"{PREFIX}/demo/reset", json={"confirm": "yes", "actor_id": "operator-1"}
        )

        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "VALIDATION_FAILED"
        assert error["details"]["expected"] == RESET_CONFIRMATION
        assert "resolution" in error["details"]

    def test_nothing_is_removed_by_a_refused_reset(self, client, board):
        before = _dataset(client)["flights"]
        client.post(f"{PREFIX}/demo/reset", json={"confirm": "nope"})

        assert _dataset(client)["flights"] == before

    def test_a_missing_confirmation_is_a_validation_error(self, client, board):
        assert client.post(f"{PREFIX}/demo/reset", json={}).status_code == 422

    def test_the_confirmation_is_case_and_space_tolerant_but_not_arbitrary(self, client, board):
        """A typed phrase should survive a stray capital, but nothing else counts."""
        response = client.post(f"{PREFIX}/demo/reset", json={"confirm": "  Reset Demo Data  "})

        assert response.status_code != 422

    def test_an_unsafe_environment_cannot_start_the_app_at_all(self, monkeypatch):
        """Where the environment guarantee actually lives, which is not at request time.

        `_refuse_outside_demo` compares `app_env` against {development, demo, test} — and `AppEnv`
        contains exactly those three values, so the comparison can never fail. That is not a hole:
        an unrecognised `APP_ENV` is refused when `Settings` is constructed, so the process does not
        boot rather than booting and refusing one route. Failing at startup is the stronger of the
        two, and this test pins it so the endpoint's guard is never mistaken for the whole story.
        """
        from pydantic import ValidationError

        from app.config import Settings

        monkeypatch.setenv("APP_ENV", "production")
        with pytest.raises(ValidationError, match="app_env"):
            Settings()

    def test_the_guard_is_kept_in_the_endpoint_as_defence_in_depth(self):
        """It costs nothing and it is the same call the CLI makes.

        Asserted structurally: if a future `AppEnv` gains a permissive-but-unsafe member, the
        endpoint already refuses it without anybody having to remember to add the check back.
        """
        import inspect

        from app.api import demo

        assert "_refuse_outside_demo()" in inspect.getsource(demo.reset_demo)

    def test_the_dataset_surface_reports_the_environment_it_is_running_in(self, client, board):
        """`reset_allowed` tracks the guard rather than restating a constant.

        It is `True` in every environment this build can legally run in, and it is published anyway
        so the console renders a real answer instead of assuming one.
        """
        body = _dataset(client)

        assert body["app_env"] in {"development", "demo", "test"}
        assert body["reset_allowed"] is True


class TestResetRestoresTheDataset:
    def test_it_reports_what_it_seeded(self, client, board):
        response = client.post(f"{PREFIX}/demo/reset", json={"confirm": RESET_CONFIRMATION})

        assert response.status_code == 200, response.json()
        body = response.json()
        assert body["dataset_digest"]
        assert sum(body["seeded"].values()) > 0
        assert body["performed_by"] == "operator-1"

    def test_it_leaves_a_seeded_dataset_behind(self, client, board):
        client.post(f"{PREFIX}/demo/reset", json={"confirm": RESET_CONFIRMATION})

        body = _dataset(client)
        assert body["is_seeded"] is True
        assert body["flights"] > 3, "the real seeded board is larger than the test fixture"

    def test_it_does_not_open_a_cascade(self, client, board):
        """Reset restores; opening is a separate operation. A control that did both would make
        "what will this do?" unanswerable from the button."""
        response = client.post(f"{PREFIX}/demo/reset", json={"confirm": RESET_CONFIRMATION})

        assert response.json()["seeded_group_reference"]
        after = _dataset(client)
        assert after["incidents"] == 0
        assert after["current_group_reference"] is None

    def test_the_note_explains_why_nothing_is_in_progress(self, client, board):
        note = client.post(f"{PREFIX}/demo/reset", json={"confirm": RESET_CONFIRMATION}).json()[
            "note"
        ]

        assert "no cascade is open" in note

    def test_the_catalogue_is_runnable_after_a_reset(self, client, board):
        """The end an operator cares about: reset, then run a simulation, with no terminal."""
        client.post(f"{PREFIX}/demo/reset", json={"confirm": RESET_CONFIRMATION})

        body = _simulations(client)
        assert body["runnable_count"] >= 1
