"""The passenger-facing read of a disruption — Phase 5, Stream A.

`GET /passenger/{booking_ref}/disruption` answers, for one booking, the question the operator
console answers for a whole cascade: what happened, what it did to this trip, what has been done
about it, and what happens next.

## It is a projection, and only a projection

Every field traces to a row somebody wrote. This module runs **no derivation that a service
already owns**:

    who broke a connection      read from the recorded `check_connections` payload
    how constrained they are    read from the `passenger_impact` row
    which rooms are theirs      read from `hotel_reservation.booking_id`
    what was authorised         read from `assurance_evaluation` + `human_decision`
    what executed              read from `action`

It aggregates nothing. There is no sum, no count beyond the length of a list it returns, and no
second definition of a figure a service already publishes — the rule `test_phase2_guards` pins for
the group surface, applied here by hand because the same failure would be worse on this screen: an
operator can cross-check a wrong number against the ledger, and a passenger cannot.

## Two things it will not do

**It will not name the passenger.** `Passenger.full_name`, `.email` and `.phone` are in the
database and are not in the response schema, so there is no field to leak them into. The reader
already knows who they are; what they need is their PNR and the synthetic reference an agent can
quote back.

**It will not promise a seat.** The connection service records "later departures on this city pair
that the schedule says are reachable", explicitly not availability, because this system holds no
capacity data at all. That claim travels into the response as a `Literal` basis, so the option
cannot be rendered as an offer without changing the type.

## Empty states are answers, not errors

An unknown PNR is a 404 — the one case where nothing can be said. A booking with no incident on any
segment is a **200** with `disruption: null` and `next_step.state = "no_disruption"`: the trip is
recorded and nothing is wrong with it, which is a different fact from "we have no idea".

Owner: Stream A.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.errors import EntityNotFound
from app.models.cascade import PassengerImpact
from app.models.enums import ActionStatus, ActionType, AssuranceDecision, ProvenanceKind
from app.models.reference import Booking, BookingSegment, Flight, Hotel, Passenger
from app.models.workflow import (
    Action,
    AssuranceEvaluation,
    HotelReservation,
    HumanDecision,
    Incident,
    IncidentGroup,
    Plan,
    PlanTask,
)
from app.schemas.cascade import ImpactFactorOut, ProvenanceBlock, UnassessedFactorOut
from app.schemas.passenger import (
    ConnectionImpactOut,
    DisruptionOut,
    NextStepOut,
    PassengerActionOut,
    PassengerDisruptionResponse,
    PriorityOut,
    RecoveryOptionOut,
    SegmentOut,
    TripOut,
)
from app.services.passenger_impact import UNASSESSED_FACTORS

router = APIRouter(tags=["passenger"])

#: The caption that travels with the payload, so a screenshot cannot separate it from the figures.
NOTE = (
    "Read from recorded rows for this booking only. Alternative flights are schedule feasible, "
    "never an available seat: this system holds no capacity data. No compensation figure is "
    "stated here — entitlements are decided by the policy engine against a reviewed pack. A null "
    "field means nothing is recorded, never that nothing is wrong."
)

#: Flight statuses the board publishes. Anything else is normalised to `scheduled` rather than
#: being passed through, so a value nobody designed for cannot reach a passenger's screen.
_KNOWN_SEGMENT_STATUS = frozenset({"on_time", "scheduled", "delayed", "cancelled"})


def _as_utc(value: datetime | None) -> datetime | None:
    """Label a naive timestamp as UTC. Storage is always UTC; display converts, not this."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _delay_minutes(flight: Flight) -> int | None:
    """Published delay, or `None` when the airline has revised nothing.

    `None` and `0` are different claims and are kept apart all the way to the screen: one means
    nobody has said anything, the other means it is running on time.
    """
    scheduled = _as_utc(flight.scheduled_departure)
    estimated = _as_utc(flight.estimated_departure)
    if estimated is None or scheduled is None:
        return None
    return int((estimated - scheduled).total_seconds() // 60)


async def _load_booking(session: AsyncSession, booking_ref: str) -> tuple[Booking, Passenger]:
    """Resolve the PNR the passenger holds.

    Uppercased because a PNR is uppercase by construction and a reader typing it in lower case has
    not made a mistake worth a 404. Nothing else is coerced.
    """
    key = booking_ref.strip().upper()
    row = (
        await session.execute(
            select(Booking, Passenger)
            .join(Passenger, Booking.passenger_id == Passenger.id)
            .where(Booking.pnr == key)
            .limit(1)
        )
    ).first()
    if row is None:
        raise EntityNotFound(
            "no booking matches that reference",
            details={
                "booking_ref": key,
                "resolution": (
                    "Check the booking reference from the ticket. Seeded demo bookings are listed "
                    "by GET /incident-groups/{ref}/impact."
                ),
            },
        )
    booking, passenger = row
    return booking, passenger


async def _segment_rows(
    session: AsyncSession, booking_id: int
) -> list[tuple[BookingSegment, Flight]]:
    """The journey as sold, in the order it is flown."""
    return list(
        (
            await session.execute(
                select(BookingSegment, Flight)
                .join(Flight, BookingSegment.flight_id == Flight.id)
                .where(BookingSegment.booking_id == booking_id)
                .order_by(BookingSegment.segment_order)
            )
        ).all()
    )


async def _incidents_for(session: AsyncSession, flight_ids: list[int]) -> list[Incident]:
    """Every incident opened against a flight this booking is on, newest first."""
    if not flight_ids:
        return []
    return list(
        (
            await session.execute(
                select(Incident)
                .where(Incident.flight_id.in_(flight_ids))
                .order_by(Incident.opened_at.desc(), Incident.id.desc())
            )
        )
        .scalars()
        .all()
    )


async def _current_plan_id(session: AsyncSession, incident_id: int) -> int | None:
    """The plan the engine is driving: the selected one, else the earliest.

    Deliberately not the latest. Once the planner agent adds a candidate alongside the playbook,
    the latest plan is frequently the one that is *not* executing, and showing a passenger work
    from a plan nobody is running would misreport their trip. Mirrors `_plan_summary` in
    `incidents.py` and `Orchestrator._current_plan`.
    """
    return (
        (
            await session.execute(
                select(Plan.id)
                .where(Plan.incident_id == incident_id)
                .order_by(case((Plan.selection_state == "selected", 0), else_=1), Plan.id)
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


async def _recorded_connection(
    session: AsyncSession, *, incident_ids: list[int], booking_id: int
) -> tuple[dict[str, Any], int] | None:
    """This booking's broken connection, as the connection service recorded it.

    Scanned across every incident in scope rather than one, because the walk is performed by
    whichever incident owns the inbound flight — for a multi-leg itinerary that need not be the
    incident the passenger's first segment belongs to.

    A lookup, not an aggregation: it finds the one entry naming this booking and returns it
    untouched. Recomputing "did this connection hold" here would be a second implementation of a
    service's answer, and the passenger would have no way to tell which was wrong.
    """
    if not incident_ids:
        return None
    rows = (
        await session.execute(
            select(Action)
            .join(PlanTask, Action.plan_task_id == PlanTask.id)
            .join(Plan, PlanTask.plan_id == Plan.id)
            .where(
                Plan.incident_id.in_(incident_ids),
                PlanTask.action_type == ActionType.check_connections.value,
                Action.status == ActionStatus.success.value,
            )
            .order_by(Action.id.desc())
        )
    ).scalars()
    for action in rows:
        payload = action.payload if isinstance(action.payload, dict) else {}
        entries = payload.get("at_risk")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and entry.get("booking_id") == booking_id:
                return entry, action.id
    return None


def _connection_out(entry: dict[str, Any], action_id: int) -> ConnectionImpactOut | None:
    """Project the recorded entry, or nothing if it is not the shape the service writes.

    Defensive rather than trusting: this reads a JSON column, and a payload from an older rule
    version that no longer carries a field must produce an absent connection rather than a
    half-built one asserting times nobody recorded.
    """
    required = (
        "inbound_flight_number",
        "onward_flight_number",
        "connection_airport_icao",
        "inbound_scheduled_arrival",
        "inbound_revised_arrival",
        "onward_scheduled_departure",
        "minimum_connection_minutes",
        "shortfall_minutes",
    )
    if any(entry.get(key) is None for key in required):
        return None
    return ConnectionImpactOut(
        inbound_flight_number=str(entry["inbound_flight_number"]),
        onward_flight_number=str(entry["onward_flight_number"]),
        connection_airport_icao=str(entry["connection_airport_icao"]),
        inbound_scheduled_arrival=entry["inbound_scheduled_arrival"],
        inbound_revised_arrival=entry["inbound_revised_arrival"],
        onward_scheduled_departure=entry["onward_scheduled_departure"],
        minimum_connection_minutes=int(entry["minimum_connection_minutes"]),
        shortfall_minutes=int(entry["shortfall_minutes"]),
        recovered_by_onward_delay=bool(entry.get("recovered_by_onward_delay", False)),
        established_by_action_id=action_id,
    )


async def _alternative_options(
    session: AsyncSession, flight_ids: list[int]
) -> list[RecoveryOptionOut]:
    """Later departures the recorded assessment found reachable.

    `basis` is pinned to `schedule_feasible_only` by the schema. There is no seat data anywhere in
    this system, so this says the departure is late enough to be caught and nothing more.
    """
    if not flight_ids:
        return []
    flights = (
        (
            await session.execute(
                select(Flight).where(Flight.id.in_(flight_ids)).order_by(Flight.scheduled_departure)
            )
        )
        .scalars()
        .all()
    )
    return [
        RecoveryOptionOut(
            kind="alternative_flight",
            label=f"{flight.flight_number} {flight.origin_icao} to {flight.destination_icao}",
            basis="schedule_feasible_only",
            flight_id=flight.id,
            flight_number=flight.flight_number,
            scheduled_departure=_as_utc(flight.scheduled_departure),
            requires_agent=True,
        )
        for flight in flights
    ]


async def _hotel_options(session: AsyncSession, booking_id: int) -> list[RecoveryOptionOut]:
    """Rooms recorded against this booking.

    `is_simulated` decides the basis, because "a room is held" and "a room would be held" are
    different promises and only one of them is true in a demo.
    """
    rows = (
        await session.execute(
            select(HotelReservation, Hotel)
            .join(Hotel, HotelReservation.hotel_id == Hotel.id)
            .where(HotelReservation.booking_id == booking_id)
            .order_by(HotelReservation.id)
        )
    ).all()
    return [
        RecoveryOptionOut(
            kind="hotel_room",
            label=f"{reservation.rooms} room at {hotel.name}",
            basis="simulated_reservation" if reservation.is_simulated else "recorded_reservation",
            hotel_name=hotel.name,
            nights=reservation.nights,
            requires_agent=True,
        )
        for reservation, hotel in rows
    ]


async def _actions_for(
    session: AsyncSession, *, plan_id: int | None, personal_action_ids: set[int]
) -> list[PassengerActionOut]:
    """Every task on the driving plan, with the approval that authorised it.

    Tasks rather than actions, because the state a passenger most needs told accurately is the one
    with no action row yet: a `needs_human` evaluation with no decision recorded against it is
    work waiting on a person, and reporting it as "pending" would hide the only step where somebody
    is deliberately in the loop.

    `applies_to` separates the two honest claims. `check_connections` assessed a whole flight, so
    "your connection was checked" is true at incident scope; a room is only *theirs* when a
    `hotel_reservation` row names their booking, which is what `personal_action_ids` carries.
    """
    if plan_id is None:
        return []

    tasks = (
        (
            await session.execute(
                select(PlanTask).where(PlanTask.plan_id == plan_id).order_by(PlanTask.task_order)
            )
        )
        .scalars()
        .all()
    )
    if not tasks:
        return []

    task_ids = [task.id for task in tasks]

    # Evaluations are append-only, so the current judgement is the newest per task.
    latest_ids = dict(
        (
            await session.execute(
                select(AssuranceEvaluation.plan_task_id, func.max(AssuranceEvaluation.id))
                .where(AssuranceEvaluation.plan_task_id.in_(task_ids))
                .group_by(AssuranceEvaluation.plan_task_id)
            )
        ).all()
    )
    evaluations = {
        evaluation.id: evaluation
        for evaluation in (
            (
                await session.execute(
                    select(AssuranceEvaluation).where(
                        AssuranceEvaluation.id.in_(list(latest_ids.values()) or [0])
                    )
                )
            )
            .scalars()
            .all()
        )
    }
    decisions = {
        decision.assurance_id: decision
        for decision in (
            (
                await session.execute(
                    select(HumanDecision).where(
                        HumanDecision.assurance_id.in_(list(latest_ids.values()) or [0])
                    )
                )
            )
            .scalars()
            .all()
        )
    }
    actions = {
        action.plan_task_id: action
        for action in (
            (
                await session.execute(
                    select(Action).where(Action.plan_task_id.in_(task_ids)).order_by(Action.id)
                )
            )
            .scalars()
            .all()
        )
    }

    out: list[PassengerActionOut] = []
    for task in tasks:
        action = actions.get(task.id)
        evaluation_id = latest_ids.get(task.id)
        evaluation = evaluations.get(evaluation_id) if evaluation_id else None
        decision = decisions.get(evaluation_id) if evaluation_id else None

        awaiting = (
            action is None
            and evaluation is not None
            and str(evaluation.decision) == AssuranceDecision.needs_human.value
            and decision is None
        )

        if action is not None:
            # `ActionStatus`, never a `TaskState` value: `success` and `succeeded` are different
            # vocabularies and comparing across them is silently always false.
            status = str(action.status)
            state = "succeeded" if status == ActionStatus.success.value else status
        elif awaiting:
            state = "awaiting_approval"
        elif str(task.state) == "executing":
            state = "executing"
        else:
            state = "pending"

        payload = action.payload if action is not None and isinstance(action.payload, dict) else {}
        reason_code = payload.get("reason_code")

        out.append(
            PassengerActionOut(
                action_type=task.action_type,
                state=state,  # type: ignore[arg-type]
                applies_to=(
                    "this_booking"
                    if action is not None and action.id in personal_action_ids
                    else "incident"
                ),
                at=_as_utc(action.executed_at) if action is not None else None,
                reason_code=reason_code if isinstance(reason_code, str) and reason_code else None,
                approval_scope=(
                    decision.scope  # type: ignore[arg-type]
                    if decision is not None and decision.scope in {"action", "plan"}
                    else None
                ),
                awaiting_human=awaiting,
            )
        )
    return out


async def _personal_action_ids(session: AsyncSession, *, booking_id: int) -> set[int]:
    """Actions that produced a row naming this booking.

    Only accommodation currently records per-booking, so this is the hotel reservation's own
    `action_id`. It is what lets the screen say "held for you" without that being a guess.
    """
    ids = (
        (
            await session.execute(
                select(HotelReservation.action_id).where(
                    HotelReservation.booking_id == booking_id,
                    HotelReservation.action_id.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    return {int(value) for value in ids if value is not None}


def _next_step(
    *, disruption: DisruptionOut | None, actions: list[PassengerActionOut]
) -> NextStepOut:
    """What happens next, from recorded state only.

    Awaiting-approval outranks executing deliberately. A passenger reading "we are arranging this"
    while a person has not yet signed the plan has been told about a transition that has not
    happened, and that is the one misreport this product exists to prevent.
    """
    if disruption is None:
        return NextStepOut(state="no_disruption")

    blocked = next((action for action in actions if action.awaiting_human), None)
    if blocked is not None:
        return NextStepOut(state="awaiting_approval", driven_by_action_type=blocked.action_type)

    running = next((action for action in actions if action.state == "executing"), None)
    if running is not None:
        return NextStepOut(state="executing", driven_by_action_type=running.action_type)

    if disruption.state == "resolved":
        return NextStepOut(state="resolved")

    return NextStepOut(state="monitoring")


def _segment_status(flight: Flight, *, onward_flight_id: int | None) -> str:
    """The recorded flight status, with one recorded finding layered on top.

    `at_risk` is not invented here: it is applied only to the onward flight the connection service
    itself named as no longer feasible, and only when the board has not already published something
    stronger. A cancelled flight is not downgraded to "at risk".
    """
    status = str(flight.status) if str(flight.status) in _KNOWN_SEGMENT_STATUS else "scheduled"
    named_by_the_assessment = onward_flight_id is not None and flight.id == onward_flight_id
    if named_by_the_assessment and status in {"on_time", "scheduled"}:
        return "at_risk"
    return status


@router.get(
    "/passenger/{booking_ref}/disruption",
    response_model=PassengerDisruptionResponse,
    summary="One booking's disruption, read from recorded rows",
)
async def get_passenger_disruption(
    booking_ref: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PassengerDisruptionResponse:
    booking, passenger = await _load_booking(session, booking_ref)

    segment_rows = await _segment_rows(session, booking.id)
    flight_ids = [flight.id for _segment, flight in segment_rows]
    incidents = await _incidents_for(session, flight_ids)
    disrupted_flight_ids = {incident.flight_id for incident in incidents}

    incident = incidents[0] if incidents else None
    group: IncidentGroup | None = None
    if incident is not None and incident.group_id is not None:
        group = await session.get(IncidentGroup, incident.group_id)

    # Connections are assessed by whichever incident owns the inbound leg, so every incident this
    # booking touches is in scope for the lookup.
    recorded = await _recorded_connection(
        session, incident_ids=[entry.id for entry in incidents], booking_id=booking.id
    )
    connection = _connection_out(*recorded) if recorded is not None else None

    onward_flight_id: int | None = None
    alternative_ids: list[int] = []
    if recorded is not None:
        entry, _action_id = recorded
        raw_onward = entry.get("onward_flight_id")
        onward_flight_id = int(raw_onward) if isinstance(raw_onward, int) else None
        raw_alternatives = entry.get("alternative_flight_ids")
        if isinstance(raw_alternatives, list):
            alternative_ids = [int(value) for value in raw_alternatives if isinstance(value, int)]

    disruption: DisruptionOut | None = None
    if incident is not None:
        incident_flight = next(
            (flight for _segment, flight in segment_rows if flight.id == incident.flight_id), None
        )
        disruption = DisruptionOut(
            incident_reference=incident.reference,
            group_reference=group.reference if group is not None else None,
            flight_id=incident.flight_id,
            flight_number=incident_flight.flight_number if incident_flight else "",
            airport_icao=(
                group.airport_icao
                if group is not None
                else (incident_flight.origin_icao if incident_flight else "")
            ),
            cause_category=str(incident.trigger_type),
            severity=str(incident.severity),
            state=str(incident.state),
            opened_at=_as_utc(incident.opened_at),
            closed_at=_as_utc(incident.closed_at),
        )

    priority: PriorityOut | None = None
    if group is not None:
        row = (
            (
                await session.execute(
                    select(PassengerImpact)
                    .where(
                        PassengerImpact.incident_group_id == group.id,
                        PassengerImpact.passenger_id == passenger.id,
                        PassengerImpact.booking_id == booking.id,
                    )
                    .order_by(PassengerImpact.id.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if row is not None:
            priority = PriorityOut(
                priority_index=row.priority_index,
                priority_band=row.priority_band,
                factors=[
                    ImpactFactorOut(
                        factor=str(item.get("factor", "")),
                        weight=int(item.get("weight", 0)),
                        source=str(item.get("source", "")),
                    )
                    for item in (row.factors or [])
                    if isinstance(item, dict)
                ],
                rule_version=row.rule_version,
                ruleset_hash=row.ruleset_hash,
            )

    personal = await _personal_action_ids(session, booking_id=booking.id)
    plan_id = await _current_plan_id(session, incident.id) if incident is not None else None
    actions = await _actions_for(session, plan_id=plan_id, personal_action_ids=personal)

    options = [
        *await _alternative_options(session, alternative_ids),
        *await _hotel_options(session, booking.id),
    ]

    return PassengerDisruptionResponse(
        booking_ref=booking.pnr,
        passenger_reference=passenger.reference,
        cabin=booking.cabin,
        tier=passenger.tier,
        has_special_needs=passenger.has_special_needs,
        trip=TripOut(
            origin_icao=segment_rows[0][1].origin_icao if segment_rows else "",
            destination_icao=segment_rows[-1][1].destination_icao if segment_rows else "",
            segments=[
                SegmentOut(
                    segment_id=segment.id,
                    segment_order=segment.segment_order,
                    flight_id=flight.id,
                    flight_number=flight.flight_number,
                    origin_icao=flight.origin_icao,
                    destination_icao=flight.destination_icao,
                    scheduled_departure=_as_utc(flight.scheduled_departure),
                    scheduled_arrival=_as_utc(flight.scheduled_arrival),
                    estimated_departure=_as_utc(flight.estimated_departure),
                    delay_minutes=_delay_minutes(flight),
                    status=_segment_status(flight, onward_flight_id=onward_flight_id),  # type: ignore[arg-type]
                    gate=flight.gate,
                    is_disrupted=flight.id in disrupted_flight_ids,
                )
                for segment, flight in segment_rows
            ],
        ),
        disruption=disruption,
        connection=connection,
        priority=priority,
        options=options,
        actions=actions,
        next_step=_next_step(disruption=disruption, actions=actions),
        unassessed_factors=[
            UnassessedFactorOut(factor=factor, reason=reason, established_by=established_by)
            for factor, reason, established_by in UNASSESSED_FACTORS
        ],
        note=NOTE,
        provenance=ProvenanceBlock(
            kind=str(ProvenanceKind(str(passenger.provenance_kind))),
            provider="booking_records",
            source_ref=f"booking:{booking.id}",
        ),
    )
