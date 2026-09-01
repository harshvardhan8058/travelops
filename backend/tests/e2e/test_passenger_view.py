"""The passenger view reads recorded rows, and says nothing else — Phase 5.

The screen this endpoint feeds is read by the person least able to check it. An operator who is
told the wrong thing can open the ledger; a passenger cannot. So the tests here are weighted
towards what the endpoint must REFUSE to say:

  * no name, email or phone, ever, from any field;
  * no compensation figure;
  * no seat, only a reachable departure;
  * no "arranged" for work a human has not approved;
  * no zero standing in for "nothing recorded".

The happy path is asserted too, but the absences are the point.

Owner: Stream A.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.cascade import PassengerImpact
from app.models.enums import (
    ActionStatus,
    AssuranceDecision,
    ProvenanceKind,
    RiskTier,
    TaskState,
)
from app.models.reference import Booking, BookingSegment, Flight, Hotel, Passenger
from app.models.workflow import (
    Action,
    AssuranceEvaluation,
    HotelReservation,
    HumanDecision,
    Plan,
    PlanTask,
)

PREFIX = "/api/v1"
PNR = "QT7HJ2"
#: The gate stamps every evaluation with the config it judged under; the column is NOT NULL.
CONFIG_VERSION = "assurance-v1"
CONFIG_HASH = "f3964eb196257d1d"
GROUP_REFERENCE = "GRP-2026-0820-VOBL"
DEPARTURE = datetime(2026, 8, 20, 15, 40, tzinfo=UTC)


def _factory(engine):
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@pytest.fixture
async def booked(seeded):
    """One passenger holding a two-leg itinerary through the disrupted VOBL departure.

    The onward leg is a real `flight` row rather than a literal in a payload, because the endpoint
    resolves alternatives and onward legs by id and a test against invented ids would pass while
    the real join was broken.
    """
    async with _factory(seeded)() as db:
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
        later = Flight(
            flight_number="AI 305",
            airline_code="AI",
            origin_icao="VIDP",
            destination_icao="VOBL",
            scheduled_departure=DEPARTURE + timedelta(minutes=800),
            scheduled_arrival=DEPARTURE + timedelta(minutes=965),
            estimated_departure=None,
            block_time_minutes=165,
            status="scheduled",
            provenance_kind=ProvenanceKind.fixture,
            source_ref="test:later",
        )
        db.add_all([onward, later])
        await db.flush()

        passenger = Passenger(
            reference="PAX-00001",
            full_name="Ada Lovelace",
            email="pax-00001@example.com",
            phone="+91 90000 00001",
            tier="gold",
            has_special_needs=False,
            provenance_kind=ProvenanceKind.synthetic,
        )
        db.add(passenger)
        await db.flush()

        booking = Booking(pnr=PNR, passenger_id=passenger.id, cabin="economy")
        db.add(booking)
        await db.flush()

        db.add_all(
            [
                BookingSegment(booking_id=booking.id, flight_id=1, segment_order=1),
                BookingSegment(booking_id=booking.id, flight_id=onward.id, segment_order=2),
            ]
        )
        await db.commit()
        return {
            "booking_id": booking.id,
            "passenger_id": passenger.id,
            "onward_flight_id": onward.id,
            "later_flight_id": later.id,
        }


async def _record_connection_action(
    engine, *, incident_reference: str, booked: dict, status: str = ActionStatus.success.value
) -> int:
    """Persist a `check_connections` action exactly as the service would.

    Built through the real models — plan, task, evaluation, action — because the endpoint reaches
    the payload through those joins. An action cannot exist without an evaluation, and the test
    honours that rather than reaching around it.
    """
    from app.models.workflow import Incident

    async with _factory(engine)() as db:
        incident = (
            (
                await db.execute(
                    __import__("sqlalchemy")
                    .select(Incident)
                    .where(Incident.reference == incident_reference)
                )
            )
            .scalars()
            .one()
        )

        plan = Plan(incident_id=incident.id, generator="fallback-playbook")
        db.add(plan)
        await db.flush()

        task = PlanTask(
            plan_id=plan.id,
            action_type="check_connections",
            task_order=1,
            target_refs=[f"flight:{incident.flight_id}"],
            state=TaskState.succeeded,
        )
        db.add(task)
        await db.flush()

        evaluation = AssuranceEvaluation(
            plan_task_id=task.id,
            decision=AssuranceDecision.execute,
            risk_tier=RiskTier.low,
            check_results=[],
            config_version=CONFIG_VERSION,
            config_hash=CONFIG_HASH,
        )
        db.add(evaluation)
        await db.flush()

        action = Action(
            plan_task_id=task.id,
            assurance_id=evaluation.id,
            actor="orchestrator",
            idempotency_key=f"check_connections:{task.id}:test",
            status=status,
            reason="1 itinerary no longer feasible",
            executed_at=DEPARTURE + timedelta(minutes=5),
            provenance_kind=ProvenanceKind.synthetic,
            payload={
                "rule_version": "connection-v1",
                "minimum_connection_minutes": 45,
                "at_risk_count": 1,
                "at_risk": [
                    {
                        "booking_id": booked["booking_id"],
                        "pnr": PNR,
                        "passenger_id": booked["passenger_id"],
                        "passenger_reference": "PAX-00001",
                        "tier": "gold",
                        "has_special_needs": False,
                        "inbound_segment_id": 1,
                        "inbound_flight_id": 1,
                        "inbound_flight_number": "6E 2134",
                        "inbound_scheduled_arrival": "2026-08-20T18:25:00Z",
                        "inbound_revised_arrival": "2026-08-20T22:45:00Z",
                        "inbound_delay_minutes": 420,
                        "onward_segment_id": 2,
                        "onward_flight_id": booked["onward_flight_id"],
                        "onward_flight_number": "AI 101",
                        "onward_scheduled_departure": "2026-08-20T20:40:00Z",
                        "connection_airport_icao": "VIDP",
                        "minimum_connection_minutes": 45,
                        "shortfall_minutes": -170,
                        "slack_minutes": -170,
                        "alternative_flight_ids": [booked["later_flight_id"]],
                        "alternatives_basis": "schedule_feasible_only",
                        "recovered_by_onward_delay": False,
                    }
                ],
            },
        )
        db.add(action)
        await db.commit()
        return action.id


async def _attach_group(engine, *, incident_reference: str) -> int:
    """Put the incident in a group, which is the scope priority rows are keyed on."""
    import sqlalchemy

    from app.models.workflow import Incident, IncidentGroup

    async with _factory(engine)() as db:
        group = IncidentGroup(
            reference=GROUP_REFERENCE,
            root_cause="weather",
            airport_icao="VOBL",
            severity="high",
            state="detected",
            opened_at=DEPARTURE,
        )
        db.add(group)
        await db.flush()
        incident = (
            (
                await db.execute(
                    sqlalchemy.select(Incident).where(Incident.reference == incident_reference)
                )
            )
            .scalars()
            .one()
        )
        incident.group_id = group.id
        await db.commit()
        return group.id


def _get(client, pnr: str = PNR):
    return client.get(f"{PREFIX}/passenger/{pnr}/disruption")


# ---------------------------------------------------------------- empty states


class TestTheEmptyStates:
    def test_an_unknown_reference_is_a_typed_404(self, client, booked):
        response = _get(client, "ZZZZZZ")

        assert response.status_code == 404
        error = response.json()["error"]
        assert error["code"] == "ENTITY_NOT_FOUND"
        assert error["correlation_id"]
        assert "resolution" in error["details"], "a 404 must say what to do next"

    def test_an_undisrupted_booking_is_a_200_not_a_404(self, client, booked):
        """A recorded trip with nothing wrong is an answer, not a missing record."""
        body = _get(client).json()

        assert body["disruption"] is None
        assert body["next_step"]["state"] == "no_disruption"
        assert body["booking_ref"] == PNR

    def test_an_undisrupted_booking_invents_no_options_or_actions(self, client, booked):
        body = _get(client).json()

        assert body["options"] == []
        assert body["actions"] == []
        assert body["connection"] is None
        assert body["priority"] is None

    def test_a_lowercase_reference_still_resolves(self, client, booked):
        assert _get(client, PNR.lower()).status_code == 200


# ---------------------------------------------------------------- no personal data


class TestNoPersonalDataLeaves:
    def test_the_response_carries_no_name_email_or_phone(self, client, booked, incident):
        """The strongest form: scan the whole serialised body, not a field list.

        The seeded passenger has a name, an address and a number. If any future field starts
        carrying one, this fails without anybody having to remember to check.
        """
        raw = json.dumps(_get(client).json())

        assert "Ada" not in raw
        assert "Lovelace" not in raw
        assert "@example.com" not in raw
        assert "90000" not in raw

    def test_only_the_pseudonymous_reference_identifies_the_passenger(self, client, booked):
        body = _get(client).json()

        assert body["passenger_reference"] == "PAX-00001"
        assert "passenger_name" not in body

    def test_the_schema_has_no_field_for_personal_data(self):
        """Absence by construction beats absence by discipline."""
        from app.schemas.passenger import PassengerDisruptionResponse

        fields = set(PassengerDisruptionResponse.model_fields)
        assert not fields & {"passenger_name", "full_name", "email", "phone", "contact"}


# ---------------------------------------------------------------- the trip


class TestTheTripIsWhatWasRecorded:
    def test_segments_come_back_in_the_order_they_are_flown(self, client, booked):
        segments = _get(client).json()["trip"]["segments"]

        assert [segment["segment_order"] for segment in segments] == [1, 2]
        assert [segment["flight_number"] for segment in segments] == ["6E 2134", "AI 101"]

    def test_the_route_uses_recorded_icao_not_an_invented_iata_mapping(self, client, booked):
        trip = _get(client).json()["trip"]

        assert trip["origin_icao"] == "VOBL"
        assert trip["destination_icao"] == "VOBL"
        assert trip["segments"][0]["destination_icao"] == "VIDP"

    def test_the_delay_is_computed_from_the_published_estimate(self, client, booked):
        """420 minutes: the same figure the operator console derives for this flight."""
        segments = _get(client).json()["trip"]["segments"]

        assert segments[0]["delay_minutes"] == 420
        assert segments[0]["estimated_departure"] is not None

    def test_an_unrevised_leg_reports_null_rather_than_zero(self, client, booked):
        """Null is "nobody has said anything"; zero would be "it is on time"."""
        segments = _get(client).json()["trip"]["segments"]

        assert segments[1]["estimated_departure"] is None
        assert segments[1]["delay_minutes"] is None

    def test_only_a_leg_with_an_incident_is_marked_disrupted(self, client, booked, incident):
        segments = _get(client).json()["trip"]["segments"]

        assert segments[0]["is_disrupted"] is True
        assert segments[1]["is_disrupted"] is False


# ---------------------------------------------------------------- the disruption


class TestTheDisruptionIsTheRecordedIncident:
    def test_it_names_the_real_incident(self, client, booked, incident):
        disruption = _get(client).json()["disruption"]

        assert disruption["incident_reference"] == incident
        assert disruption["flight_number"] == "6E 2134"
        assert disruption["cause_category"] == "weather"
        assert disruption["state"] == "detected"

    def test_the_cause_is_operational_and_carries_no_liability_verdict(
        self, client, booked, incident
    ):
        disruption = _get(client).json()["disruption"]

        assert disruption["cause_category"] == "weather"
        assert "fault" not in json.dumps(disruption).lower()
        assert "liable" not in json.dumps(disruption).lower()


# ---------------------------------------------------------------- the connection


class TestTheConnectionIsReadNotRecomputed:
    async def test_the_broken_connection_is_projected_from_the_recorded_action(
        self, client, booked, incident, seeded
    ):
        action_id = await _record_connection_action(
            seeded, incident_reference=incident, booked=booked
        )

        connection = _get(client).json()["connection"]

        assert connection["inbound_flight_number"] == "6E 2134"
        assert connection["onward_flight_number"] == "AI 101"
        assert connection["connection_airport_icao"] == "VIDP"
        assert connection["shortfall_minutes"] == -170
        assert connection["minimum_connection_minutes"] == 45
        assert connection["established_by_action_id"] == action_id, "the claim must be traceable"

    async def test_another_bookings_broken_connection_is_not_borrowed(
        self, client, booked, incident, seeded
    ):
        """The payload lists every broken itinerary. Only this booking's entry may be reported."""
        import sqlalchemy

        await _record_connection_action(seeded, incident_reference=incident, booked=booked)
        async with _factory(seeded)() as db:
            action = (await db.execute(sqlalchemy.select(Action))).scalars().one()
            payload = dict(action.payload)
            payload["at_risk"] = [{**payload["at_risk"][0], "booking_id": 999, "pnr": "OTHER1"}]
            action.payload = payload
            await db.commit()

        assert _get(client).json()["connection"] is None

    async def test_the_onward_leg_is_marked_at_risk_only_once_assessed(
        self, client, booked, incident, seeded
    ):
        before = _get(client).json()["trip"]["segments"][1]["status"]
        await _record_connection_action(seeded, incident_reference=incident, booked=booked)
        after = _get(client).json()["trip"]["segments"][1]["status"]

        assert before == "scheduled", "at risk is a finding, not a default"
        assert after == "at_risk"


# ---------------------------------------------------------------- options


class TestOptionsPromiseOnlyWhatIsRecorded:
    async def test_an_alternative_flight_is_schedule_feasible_only(
        self, client, booked, incident, seeded
    ):
        """The one claim this system can make about a later departure."""
        await _record_connection_action(seeded, incident_reference=incident, booked=booked)

        options = _get(client).json()["options"]
        alternatives = [option for option in options if option["kind"] == "alternative_flight"]

        assert len(alternatives) == 1
        assert alternatives[0]["flight_number"] == "AI 305"
        assert alternatives[0]["basis"] == "schedule_feasible_only"

    def test_the_contract_cannot_express_an_available_seat(self):
        """A type-level guarantee: there is no basis value that means availability."""
        import typing

        from app.schemas.passenger import RecoveryOptionOut

        allowed = set(typing.get_args(RecoveryOptionOut.model_fields["basis"].annotation))
        assert allowed == {
            "schedule_feasible_only",
            "recorded_reservation",
            "simulated_reservation",
        }

    async def test_a_hotel_option_appears_only_when_a_row_names_this_booking(
        self, client, booked, incident, seeded
    ):
        assert _get(client).json()["options"] == []

        async with _factory(seeded)() as db:
            hotel = Hotel(
                name="Airport Inn",
                airport_icao="VIDP",
                rate_inr=4200,
                is_partner=True,
                distance_km=3.5,
                total_rooms=100,
                available_rooms=40,
                provenance_kind=ProvenanceKind.synthetic,
            )
            db.add(hotel)
            await db.flush()
            db.add(
                HotelReservation(
                    hotel_id=hotel.id,
                    booking_id=booked["booking_id"],
                    rooms=1,
                    nights=1,
                    rate_inr=4200,
                    is_simulated=True,
                )
            )
            await db.commit()

        options = _get(client).json()["options"]
        assert [option["kind"] for option in options] == ["hotel_room"]
        assert options[0]["hotel_name"] == "Airport Inn"
        assert options[0]["basis"] == "simulated_reservation", "a simulated hold is not a real one"

    def test_no_refund_meal_or_transport_option_is_ever_offered(self, client, booked, incident):
        """Nothing records them, so nothing may offer them."""
        import typing

        from app.schemas.passenger import RecoveryOptionOut

        kinds = set(typing.get_args(RecoveryOptionOut.model_fields["kind"].annotation))
        assert kinds == {"alternative_flight", "hotel_room"}

    def test_no_compensation_figure_appears_anywhere(self, client, booked, incident):
        """No money field exists, at any depth.

        Checked structurally rather than by scanning for the word: the `note` deliberately SAYS
        that no compensation is stated here, and a substring search would fail on the very
        sentence that makes the guarantee.
        """
        from app.schemas.passenger import PassengerDisruptionResponse

        def money_fields(model: type, seen: set[type]) -> list[str]:
            found: list[str] = []
            if model in seen:
                return found
            seen.add(model)
            for name, field in getattr(model, "model_fields", {}).items():
                if any(token in name for token in ("inr", "amount", "compensation", "fare")):
                    found.append(name)
                for candidate in (field.annotation, *getattr(field.annotation, "__args__", ())):
                    if hasattr(candidate, "model_fields"):
                        found.extend(money_fields(candidate, seen))
            return found

        assert money_fields(PassengerDisruptionResponse, set()) == []


# ---------------------------------------------------------------- approval


class TestApprovalStateIsReportedHonestly:
    async def test_a_needs_human_evaluation_with_no_decision_is_awaiting_approval(
        self, client, booked, incident, seeded
    ):
        import sqlalchemy

        from app.models.workflow import Incident

        async with _factory(seeded)() as db:
            row = (
                (
                    await db.execute(
                        sqlalchemy.select(Incident).where(Incident.reference == incident)
                    )
                )
                .scalars()
                .one()
            )
            plan = Plan(incident_id=row.id, generator="fallback-playbook")
            db.add(plan)
            await db.flush()
            task = PlanTask(
                plan_id=plan.id,
                action_type="notify_passengers",
                task_order=1,
                state=TaskState.needs_human,
            )
            db.add(task)
            await db.flush()
            db.add(
                AssuranceEvaluation(
                    plan_task_id=task.id,
                    decision=AssuranceDecision.needs_human,
                    risk_tier=RiskTier.high,
                    check_results=[],
                    config_version=CONFIG_VERSION,
                    config_hash=CONFIG_HASH,
                )
            )
            await db.commit()

        actions = _get(client).json()["actions"]

        assert len(actions) == 1
        assert actions[0]["action_type"] == "notify_passengers"
        assert actions[0]["state"] == "awaiting_approval"
        assert actions[0]["awaiting_human"] is True
        assert actions[0]["at"] is None, "nothing has executed, so there is no timestamp"

    async def test_the_next_step_says_awaiting_approval_rather_than_executing(
        self, client, booked, incident, seeded
    ):
        """The misreport this endpoint exists to prevent."""
        import sqlalchemy

        from app.models.workflow import Incident

        async with _factory(seeded)() as db:
            row = (
                (
                    await db.execute(
                        sqlalchemy.select(Incident).where(Incident.reference == incident)
                    )
                )
                .scalars()
                .one()
            )
            plan = Plan(incident_id=row.id, generator="fallback-playbook")
            db.add(plan)
            await db.flush()
            task = PlanTask(
                plan_id=plan.id,
                action_type="rebook_passengers",
                task_order=1,
                state=TaskState.needs_human,
            )
            db.add(task)
            await db.flush()
            db.add(
                AssuranceEvaluation(
                    plan_task_id=task.id,
                    decision=AssuranceDecision.needs_human,
                    risk_tier=RiskTier.high,
                    check_results=[],
                    config_version=CONFIG_VERSION,
                    config_hash=CONFIG_HASH,
                )
            )
            await db.commit()

        next_step = _get(client).json()["next_step"]

        assert next_step["state"] == "awaiting_approval"
        assert next_step["driven_by_action_type"] == "rebook_passengers"
        assert next_step["respond_by"] is None, "no deadline is recorded, so none is shown"

    async def test_a_recorded_decision_carries_its_scope(self, client, booked, incident, seeded):
        import sqlalchemy

        from app.models.workflow import Incident

        async with _factory(seeded)() as db:
            row = (
                (
                    await db.execute(
                        sqlalchemy.select(Incident).where(Incident.reference == incident)
                    )
                )
                .scalars()
                .one()
            )
            plan = Plan(incident_id=row.id, generator="fallback-playbook")
            db.add(plan)
            await db.flush()
            task = PlanTask(
                plan_id=plan.id,
                action_type="notify_passengers",
                task_order=1,
                state=TaskState.succeeded,
            )
            db.add(task)
            await db.flush()
            evaluation = AssuranceEvaluation(
                plan_task_id=task.id,
                decision=AssuranceDecision.needs_human,
                risk_tier=RiskTier.high,
                check_results=[],
                config_version=CONFIG_VERSION,
                config_hash=CONFIG_HASH,
            )
            db.add(evaluation)
            await db.flush()
            decision = HumanDecision(
                assurance_id=evaluation.id,
                decision="approved",
                actor_id="operator-1",
                reason="reviewed",
                scope="action",
            )
            db.add(decision)
            await db.flush()
            db.add(
                Action(
                    plan_task_id=task.id,
                    assurance_id=evaluation.id,
                    human_decision_id=decision.id,
                    actor="orchestrator",
                    idempotency_key="notify:1:test",
                    status=ActionStatus.success.value,
                    reason="notified",
                    executed_at=DEPARTURE,
                    provenance_kind=ProvenanceKind.simulated,
                )
            )
            await db.commit()

        actions = _get(client).json()["actions"]

        assert actions[0]["state"] == "succeeded"
        assert actions[0]["approval_scope"] == "action"
        assert actions[0]["awaiting_human"] is False
        assert actions[0]["at"] is not None

    async def test_work_is_incident_scoped_unless_a_row_names_this_booking(
        self, client, booked, incident, seeded
    ):
        """ "We checked your connection" is true; "we held a room for you" needs a row."""
        await _record_connection_action(seeded, incident_reference=incident, booked=booked)

        actions = _get(client).json()["actions"]

        assert actions[0]["action_type"] == "check_connections"
        assert actions[0]["applies_to"] == "incident"


# ---------------------------------------------------------------- priority


class TestPriorityIsReadFromTheRecordedRow:
    async def test_no_priority_is_reported_before_one_is_recorded(self, client, booked, incident):
        assert _get(client).json()["priority"] is None

    async def test_a_recorded_impact_row_is_projected_with_its_factors(
        self, client, booked, incident, seeded
    ):
        """Priority is group-scoped, so the incident is attached to a group first.

        Deliberately not skipped when no group exists: a skipped test asserts nothing, and this is
        the path that decides whether a passenger sees why they were prioritised.
        """
        group_id = await _attach_group(seeded, incident_reference=incident)

        async with _factory(seeded)() as db:
            db.add(
                PassengerImpact(
                    incident_group_id=group_id,
                    passenger_id=booked["passenger_id"],
                    booking_id=booked["booking_id"],
                    priority_index=52,
                    priority_band="high",
                    factors=[
                        {"factor": "broken_connection", "weight": 30, "source": "connection"},
                        {"factor": "tier", "weight": 7, "source": "passenger.tier"},
                    ],
                    rule_version="passenger-impact-v1",
                    ruleset_hash="abc123",
                )
            )
            await db.commit()

        priority = _get(client).json()["priority"]

        assert priority is not None
        assert priority["priority_index"] == 52
        assert priority["priority_band"] == "high"
        assert [factor["factor"] for factor in priority["factors"]] == [
            "broken_connection",
            "tier",
        ]
        assert priority["rule_version"] == "passenger-impact-v1"

    async def test_another_passengers_priority_is_not_borrowed(
        self, client, booked, incident, seeded
    ):
        """The row is keyed on this passenger AND this booking. A neighbour's is not theirs."""
        group_id = await _attach_group(seeded, incident_reference=incident)

        async with _factory(seeded)() as db:
            db.add(
                PassengerImpact(
                    incident_group_id=group_id,
                    passenger_id=booked["passenger_id"] + 99,
                    booking_id=booked["booking_id"] + 99,
                    priority_index=90,
                    priority_band="critical",
                    factors=[],
                    rule_version="passenger-impact-v1",
                    ruleset_hash="abc123",
                )
            )
            await db.commit()

        assert _get(client).json()["priority"] is None

    async def test_the_group_reference_is_reported_once_the_incident_has_one(
        self, client, booked, incident, seeded
    ):
        await _attach_group(seeded, incident_reference=incident)

        assert _get(client).json()["disruption"]["group_reference"] == GROUP_REFERENCE


# ---------------------------------------------------------------- the caption


class TestThePayloadStatesWhatItIs:
    def test_the_basis_is_pinned_to_recorded_rows(self, client, booked):
        assert _get(client).json()["basis"] == "recorded_rows"

    def test_the_note_says_what_the_payload_does_not_claim(self, client, booked):
        note = _get(client).json()["note"]

        assert "schedule feasible" in note
        assert "capacity" in note
        assert "compensation" in note

    def test_unassessed_factors_are_named_rather_than_rendered_false(self, client, booked):
        """Absent because nobody looked is not the same as false."""
        factors = {entry["factor"] for entry in _get(client).json()["unassessed_factors"]}

        assert "overnight_exposure" in factors
        assert "journey_incomplete" in factors
        for entry in _get(client).json()["unassessed_factors"]:
            assert entry["established_by"], "a factor must name the service that would settle it"

    def test_provenance_travels_with_the_payload(self, client, booked):
        provenance = _get(client).json()["provenance"]

        assert provenance["kind"] == "synthetic"
        assert provenance["source_ref"].startswith("booking:")
