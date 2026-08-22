"""Binding Stream C's deterministic services to the dispatch boundary — STREAM A.

Stream C's services are deliberately pure: value objects in, `ServiceResult` out, no session
and no clock. All database access lives in Stream C's `app/db/scenario_queries.py`. So
something has to load a service's inputs and hand them over, and that something is the
orchestrator, which is the layer that knows which incident and which flights are in scope.

This module is that seam and nothing more. Each adapter:

1. reads the flight scope from the task's own `target_refs` — the plan already declares what
   it is acting on, so the scope is not re-derived or guessed here;
2. calls Stream C's loader to turn that scope into the service's input value objects;
3. calls the service and returns its `ServiceResult` unchanged.

No adapter interprets a result, adjusts a count, or substitutes a value when a loader finds
nothing. A service that cannot run says so through its own `needs_human` result, which is
already the honest answer.

Registration is explicit rather than at import time. `register_stage2_services()` is called
from the application lifespan and from the CLI, which keeps the registry empty by default so
a test can still exercise the refusal path.

Owner: Stream A (the seam) / Stream C (the services and loaders).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.scenario_queries import (
    load_business_constraints,
    load_connection_inputs,
    load_crew_impact_inputs,
)
from app.models.enums import ActionStatus, ActionType
from app.models.reference import Airport, Booking, BookingSegment, Flight, Passenger
from app.observability.logging import get_logger
from app.orchestrator import dispatch
from app.services.base import ServiceResult
from app.services.communication import CommunicationService, Recipient
from app.services.connection import ConnectionService
from app.services.crew_impact import CrewImpactService
from app.services.hotel import HotelAllocationService, HotelSearchService, load_hotel_options
from app.services.passenger_impact import (
    PassengerCohortFacts,
    PassengerImpactService,
)

log = get_logger(__name__)

#: The approved template for a delay notification. Content is Stream C's reviewed data in
#: fixtures/notifications/templates.json; this only selects which one applies.
DELAY_TEMPLATE_ID = "delay_notice"

#: Local timezone for passenger-facing times. Storage stays UTC; this is display only.
DISPLAY_TIMEZONE = "Asia/Kolkata"


def flight_ids_from(target_refs: list[str] | None) -> set[int]:
    """Read the flight scope out of the task's declared targets.

    Using `target_refs` rather than re-deriving the scope matters: the gate validated those
    exact references through `entities_valid`, so the service acts on what was authorised
    and not on a wider set assembled afterwards.
    """
    ids: set[int] = set()
    for ref in target_refs or []:
        kind, _, identifier = ref.partition(":")
        if kind == "flight" and identifier.isdigit():
            ids.add(int(identifier))
    return ids


def _unavailable(reason: str, *, evidence_refs: list[str] | None = None) -> ServiceResult:
    """No inputs, so no result. Never a success with an empty payload."""
    return ServiceResult(
        status=ActionStatus.needs_human,
        reason=reason,
        payload={"reason_code": "SERVICE_INPUTS_UNAVAILABLE"},
        evidence_refs=evidence_refs or [],
        provenance_kind="unavailable",
    )


# ------------------------------------------------------------------------------ connection


async def run_connection(
    *,
    session: AsyncSession,
    target_refs: list[str],
    evidence_refs: list[str] | None = None,
    **_ignored: Any,
) -> ServiceResult:
    flight_ids = flight_ids_from(target_refs)
    if not flight_ids:
        return _unavailable(
            "no flight is in scope for a connection check", evidence_refs=evidence_refs
        )

    itineraries, flights = await load_connection_inputs(session, flight_ids)
    constraints = await load_business_constraints(session)
    return await ConnectionService().execute(
        itineraries=itineraries,
        flights=flights,
        affected_flight_ids=flight_ids,
        business_constraints=constraints,
    )


# ----------------------------------------------------------------------------- crew impact


async def run_crew_impact(
    *,
    session: AsyncSession,
    target_refs: list[str],
    evidence_refs: list[str] | None = None,
    **_ignored: Any,
) -> ServiceResult:
    flight_ids = flight_ids_from(target_refs)
    if not flight_ids:
        return _unavailable(
            "no flight is in scope for a crew impact assessment", evidence_refs=evidence_refs
        )

    affected, pairings, flights = await load_crew_impact_inputs(session, flight_ids)
    if not affected:
        return _unavailable(
            "no scheduled flight matched the incident scope, so no pairing can be attributed",
            evidence_refs=evidence_refs,
        )
    # The service asserts list/dict types, so the loader's output is passed through as-is.
    return await CrewImpactService().execute(
        affected_flights=affected, pairings=pairings, flights=flights
    )


# --------------------------------------------------------------------------- communication


async def _recipients_for(session: AsyncSession, flight_ids: set[int]) -> list[Recipient]:
    """Build the recipient list from booking records.

    Every fact the approved template declares comes from a record: the passenger, the
    booking, the flight and the airport rows. Nothing is templated from a constant, and a
    passenger whose facts cannot be completed is simply not fabricated — the service reports
    them under `not_rendered`.
    """
    from zoneinfo import ZoneInfo

    local = ZoneInfo(DISPLAY_TIMEZONE)

    stmt = (
        select(Passenger, Booking, BookingSegment, Flight)
        .join(Booking, Booking.passenger_id == Passenger.id)
        .join(BookingSegment, BookingSegment.booking_id == Booking.id)
        .join(Flight, BookingSegment.flight_id == Flight.id)
        .where(BookingSegment.flight_id.in_(flight_ids))
        .order_by(Passenger.id)
    )
    rows = (await session.execute(stmt)).all()

    cities: dict[str, str] = {}
    for icao in {row[3].origin_icao for row in rows} | {row[3].destination_icao for row in rows}:
        airport = await session.get(Airport, icao)
        if airport is not None:
            cities[icao] = airport.city or airport.icao_code

    recipients: list[Recipient] = []
    for passenger, booking, _segment, flight in rows:
        scheduled = _as_local(flight.scheduled_departure, local)
        revised = _as_local(flight.estimated_departure, local)
        delay_minutes = _delay_minutes(flight)
        recipients.append(
            Recipient(
                passenger_id=passenger.id,
                passenger_reference=passenger.reference,
                email=passenger.email,
                facts={
                    "passenger_name": passenger.full_name,
                    "flight_number": flight.flight_number,
                    "origin_city": cities.get(flight.origin_icao, flight.origin_icao),
                    "destination_city": cities.get(
                        flight.destination_icao, flight.destination_icao
                    ),
                    "scheduled_departure_local": scheduled,
                    "revised_departure_local": revised,
                    "delay_minutes": delay_minutes,
                    "pnr": booking.pnr,
                },
            )
        )
    return recipients


def _as_local(value: Any, zone: Any) -> str | None:
    """Render a stored UTC timestamp in local time for a passenger-facing message."""
    from datetime import UTC

    if value is None:
        return None
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(zone).strftime("%d %b %Y %H:%M")


def _delay_minutes(flight: Flight) -> int | None:
    from datetime import UTC

    if flight.estimated_departure is None:
        return None
    estimated = flight.estimated_departure
    scheduled = flight.scheduled_departure
    if estimated.tzinfo is None:
        estimated = estimated.replace(tzinfo=UTC)
    if scheduled.tzinfo is None:
        scheduled = scheduled.replace(tzinfo=UTC)
    return max(0, int((estimated - scheduled).total_seconds() // 60))


async def run_communication(
    *,
    session: AsyncSession,
    target_refs: list[str],
    inputs: dict[str, Any] | None = None,
    evidence_refs: list[str] | None = None,
    **_ignored: Any,
) -> ServiceResult:
    from app.providers.notifications import get_notification_provider

    flight_ids = flight_ids_from(target_refs)
    if not flight_ids:
        return _unavailable("no flight is in scope for a notification", evidence_refs=evidence_refs)

    recipients = await _recipients_for(session, flight_ids)
    if not recipients:
        return _unavailable(
            "no booking records exist for the flights in scope, so there is nobody to notify",
            evidence_refs=evidence_refs,
        )

    template_id = str((inputs or {}).get("template_id") or DELAY_TEMPLATE_ID)
    # The provider is resolved from config, which already decided whether real email is
    # enabled and degraded to console if not. This must not second-guess that.
    return await CommunicationService().execute(
        template_id=template_id,
        recipients=recipients,
        provider=get_notification_provider(),
    )


async def load_passenger_cohort_facts(
    session: AsyncSession, flight_ids: set[int]
) -> list[PassengerCohortFacts]:
    """Build the ranking inputs from persisted rows and recorded findings.

    `connection_broken` is read from the recorded `check_connections` action rather than
    recomputed. One service owns one fact, so the priority list and the connection count cannot
    disagree — and if connections have not been assessed yet, the flag is simply false, which is
    what the record says.

    Lives here rather than in `scenario_queries` because it assembles a *service input* from
    several owners' rows, which is the orchestrator's job. It reads; it derives no domain figure.
    """
    ids = sorted(flight_ids)
    if not ids:
        return []

    broken_bookings = await _broken_booking_ids(session, ids)

    rows = (
        await session.execute(
            select(Booking, Passenger, BookingSegment.flight_id)
            .select_from(BookingSegment)
            .join(Booking, Booking.id == BookingSegment.booking_id)
            .join(Passenger, Passenger.id == Booking.passenger_id)
            .where(BookingSegment.flight_id.in_(ids))
            .order_by(Booking.id)
        )
    ).all()

    facts: list[PassengerCohortFacts] = []
    seen: set[int] = set()
    for booking, passenger, flight_id in rows:
        if booking.id in seen:
            continue
        seen.add(booking.id)
        broken = booking.id in broken_bookings
        facts.append(
            PassengerCohortFacts(
                passenger_id=passenger.id,
                passenger_reference=passenger.reference,
                booking_id=booking.id,
                pnr=booking.pnr,
                tier=passenger.tier,
                has_special_needs=bool(passenger.has_special_needs),
                contact_missing=not bool(booking.contact_info_provided_at_booking),
                connection_broken=broken,
                # Deliberately not asserted. Whether an onward option remains and whether a
                # passenger is stranded mid-itinerary are Rebooking's findings, and this
                # orchestrator does not have them yet. Claiming them would be inventing
                # evidence; leaving them false says only what the record says.
                no_onward_option_today=False,
                stranded_mid_itinerary=False,
                flight_id=int(flight_id),
            )
        )
    return facts


async def _broken_booking_ids(session: AsyncSession, flight_ids: list[int]) -> set[int]:
    """Booking ids the recorded Connection findings marked at risk."""
    from app.models.workflow import Action, Incident, Plan
    from app.models.workflow import PlanTask as PlanTaskRow

    payloads = (
        await session.execute(
            select(Action.payload)
            .join(PlanTaskRow, PlanTaskRow.id == Action.plan_task_id)
            .join(Plan, Plan.id == PlanTaskRow.plan_id)
            .join(Incident, Incident.id == Plan.incident_id)
            .where(
                PlanTaskRow.action_type == ActionType.check_connections.value,
                Action.status == ActionStatus.succeeded,
                Incident.flight_id.in_(flight_ids),
            )
        )
    ).all()

    at_risk: set[int] = set()
    for (payload,) in payloads:
        data = payload if isinstance(payload, dict) else {}
        for key in ("at_risk_booking_ids", "broken_booking_ids"):
            for value in data.get(key) or []:
                try:
                    at_risk.add(int(value))
                except (TypeError, ValueError):
                    continue
    return at_risk


# --------------------------------------------------------------------------------- hotels


async def _hotel_inputs(
    session: AsyncSession, target_refs: list[str], evidence_refs: list[str] | None
) -> tuple[list[Any], int, list[dict[str, Any]]] | ServiceResult:
    """Candidate properties and the passenger count both services need.

    Availability comes from Stream C's hold ledger, never from `hotel.available_rooms`: a mutated
    counter loses updates under concurrency and cannot be replayed.
    """
    flight_ids = flight_ids_from(target_refs)
    if not flight_ids:
        return _unavailable("no flight is in scope for hotel search", evidence_refs=evidence_refs)

    airport = await _origin_airport(session, flight_ids)
    if airport is None:
        return _unavailable(
            "the flights in scope have no recorded origin airport, so there is nowhere to "
            "look for rooms",
            evidence_refs=evidence_refs,
        )

    options = await load_hotel_options(session, airport_icao=airport)
    passengers = await _passenger_count(session, flight_ids)
    constraints = await load_business_constraints(session)
    return options, passengers, constraints


async def _origin_airport(session: AsyncSession, flight_ids: set[int]) -> str | None:
    from app.models.reference import Flight

    row = (
        await session.execute(
            select(Flight.origin_icao).where(Flight.id.in_(sorted(flight_ids))).limit(1)
        )
    ).first()
    return str(row[0]) if row else None


async def _passenger_count(session: AsyncSession, flight_ids: set[int]) -> int:
    from app.models.reference import Booking, BookingSegment

    return int(
        (
            await session.execute(
                select(func.count(func.distinct(Booking.id)))
                .select_from(BookingSegment)
                .join(Booking, Booking.id == BookingSegment.booking_id)
                .where(BookingSegment.flight_id.in_(sorted(flight_ids)))
            )
        ).scalar_one()
    )


async def run_hotel_search(
    *,
    session: AsyncSession,
    target_refs: list[str],
    evidence_refs: list[str] | None = None,
    **_ignored: Any,
) -> ServiceResult:
    inputs = await _hotel_inputs(session, target_refs, evidence_refs)
    if isinstance(inputs, ServiceResult):
        return inputs
    options, passengers, constraints = inputs
    return await HotelSearchService().execute(
        hotel_options=options,
        passengers=passengers,
        business_constraints=constraints,
    )


async def run_hotel_allocation(
    *,
    session: AsyncSession,
    target_refs: list[str],
    evidence_refs: list[str] | None = None,
    **_ignored: Any,
) -> ServiceResult:
    inputs = await _hotel_inputs(session, target_refs, evidence_refs)
    if isinstance(inputs, ServiceResult):
        return inputs
    options, passengers, constraints = inputs
    return await HotelAllocationService().execute(
        hotel_options=options,
        passengers=passengers,
        business_constraints=constraints,
    )


# ---------------------------------------------------------------------- passenger impact


async def run_passenger_impact(
    *,
    session: AsyncSession,
    target_refs: list[str],
    evidence_refs: list[str] | None = None,
    **_ignored: Any,
) -> ServiceResult:
    """Rank affected passengers, from facts that are already recorded.

    `connection_broken` comes from the recorded Connection action rather than being recomputed:
    one service owns one fact, so the ranking and the connection count cannot disagree.
    """
    flight_ids = flight_ids_from(target_refs)
    if not flight_ids:
        return _unavailable(
            "no flight is in scope for a passenger impact assessment", evidence_refs=evidence_refs
        )

    facts = await load_passenger_cohort_facts(session, flight_ids)
    if not facts:
        return _unavailable(
            "no booking records exist for the flights in scope, so there is nobody to rank",
            evidence_refs=evidence_refs,
        )
    constraints = await load_business_constraints(session)
    return await PassengerImpactService().execute(
        cohort_facts=facts,
        business_constraints=constraints,
    )


# ------------------------------------------------------------------------------- registry

#: Action -> adapter, for every Stream C service whose `execute()` is implemented.
#:
#: The six services still raising NotImplementedError are deliberately absent, so their
#: actions keep producing the explicit SERVICE_NOT_IMPLEMENTED refusal. Adding one here is
#: the whole integration step when Stream C finishes it.
STAGE2_ADAPTERS: dict[ActionType, Any] = {
    ActionType.check_connections: run_connection,
    ActionType.assess_crew_impact: run_crew_impact,
    ActionType.notify_passengers: run_communication,
    ActionType.prepare_notifications: run_communication,
    ActionType.find_hotel_options: run_hotel_search,
    ActionType.reserve_hotel_block: run_hotel_allocation,
    ActionType.rebook_passengers: run_passenger_impact,
}


def register_stage2_services() -> list[str]:
    """Bind every implemented service. Returns the action types now dispatchable."""
    for action, adapter in STAGE2_ADAPTERS.items():
        dispatch.register(action, adapter)
    registered = sorted(action.value for action in STAGE2_ADAPTERS)
    log.info("services_registered", actions=registered, count=len(registered))
    return registered
