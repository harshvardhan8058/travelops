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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.scenario_queries import (
    load_business_constraints,
    load_connection_inputs,
    load_crew_impact_inputs,
    recorded_actions,
)
from app.models.enums import ActionStatus, ActionType
from app.models.reference import Airport, Booking, BookingSegment, Flight, Passenger
from app.observability.logging import get_logger
from app.orchestrator import dispatch
from app.services.base import ServiceResult
from app.services.communication import CommunicationService, Recipient
from app.services.connection import ConnectionService
from app.services.crew_impact import CrewImpactService
from app.services.hotel import (
    HotelAllocationService,
    HotelSearchService,
    load_hotel_options,
)
from app.services.passenger_impact import PassengerCohortFacts, PassengerImpactService

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


# ------------------------------------------------------------------------- passenger impact


async def _cohort_facts_for(
    session: AsyncSession, flight_ids: set[int]
) -> list[PassengerCohortFacts]:
    """Build passenger cohort facts from persisted rows and recorded findings.

    Three of the five factors come straight off columns. `connection_broken` comes from the
    **recorded Connection action** rather than being recomputed here — one service owns one
    fact, and a second derivation would eventually disagree with the first. `overnight_exposure`
    is the schedule question "does any later departure on this route remain today", answered
    from `flight` rows and nothing else: there is no seat data in this system, so the honest
    statement is about departures, not availability.
    """
    stmt = (
        select(Passenger, Booking, BookingSegment, Flight)
        .join(Booking, Booking.passenger_id == Passenger.id)
        .join(BookingSegment, BookingSegment.booking_id == Booking.id)
        .join(Flight, BookingSegment.flight_id == Flight.id)
        .where(BookingSegment.flight_id.in_(flight_ids))
        .order_by(Passenger.id, BookingSegment.segment_order)
    )
    rows = (await session.execute(stmt)).all()
    if not rows:
        return []

    # Bookings whose onward segment the Connection service already found broken.
    incident_ids = await _incident_ids_for_flights(session, flight_ids)
    broken: set[int] = set()
    for _incident_id, _action_id, payload in await recorded_actions(
        session, incident_ids, ActionType.check_connections.value
    ):
        for item in payload.get("at_risk") or []:
            if item.get("booking_id") is not None:
                broken.add(int(item["booking_id"]))

    # Every flight on the affected routes, so "is there a later departure today" is answerable
    # without inventing one.
    routes = {(row[3].origin_icao, row[3].destination_icao) for row in rows}
    later = await _departures_by_route(session, routes)

    segments_by_booking: dict[int, list[Any]] = {}
    for _passenger, booking, segment, flight in rows:
        segments_by_booking.setdefault(int(booking.id), []).append((segment, flight))

    facts: list[PassengerCohortFacts] = []
    seen: set[int] = set()
    for passenger, booking, segment, flight in rows:
        if int(booking.id) in seen:
            continue
        seen.add(int(booking.id))
        ordered = sorted(
            segments_by_booking[int(booking.id)], key=lambda pair: pair[0].segment_order
        )
        is_mid_itinerary = len(ordered) > 1 and int(segment.segment_order) < int(
            ordered[-1][0].segment_order
        )
        facts.append(
            PassengerCohortFacts(
                passenger_id=int(passenger.id),
                passenger_reference=str(passenger.reference),
                booking_id=int(booking.id),
                pnr=str(booking.pnr),
                tier=str(passenger.tier or "standard"),
                has_special_needs=bool(passenger.has_special_needs),
                contact_missing=not bool(booking.contact_info_provided_at_booking),
                connection_broken=int(booking.id) in broken,
                no_onward_option_today=_no_later_departure(
                    flight=flight, later=later, delayed_by=_delay_minutes(flight) or 0
                ),
                stranded_mid_itinerary=is_mid_itinerary,
                flight_id=int(flight.id),
            )
        )
    return facts


async def _incident_ids_for_flights(session: AsyncSession, flight_ids: set[int]) -> list[int]:
    from app.models.workflow import Incident

    rows = (
        await session.execute(select(Incident.id).where(Incident.flight_id.in_(flight_ids)))
    ).all()
    return sorted(int(row[0]) for row in rows)


async def _departures_by_route(
    session: AsyncSession, routes: set[tuple[str, str]]
) -> dict[tuple[str, str], list[Any]]:
    """All scheduled departures per city pair, ascending. A schedule fact, not an offer."""
    if not routes:
        return {}
    origins = {origin for origin, _ in routes}
    destinations = {destination for _, destination in routes}
    rows = (
        (
            await session.execute(
                select(Flight)
                .where(Flight.origin_icao.in_(origins), Flight.destination_icao.in_(destinations))
                .order_by(Flight.scheduled_departure)
            )
        )
        .scalars()
        .all()
    )
    grouped: dict[tuple[str, str], list[Any]] = {}
    for flight in rows:
        grouped.setdefault((flight.origin_icao, flight.destination_icao), []).append(flight)
    return grouped


def _no_later_departure(*, flight: Any, later: dict, delayed_by: int) -> bool:
    """True when nothing else leaves on this route after the passenger could reach the gate.

    Same-calendar-day only, in the operating timezone, because "tomorrow morning" is an
    overnight — which is precisely the thing being detected.
    """
    from datetime import UTC, timedelta
    from zoneinfo import ZoneInfo

    zone = ZoneInfo(DISPLAY_TIMEZONE)
    scheduled = flight.scheduled_departure
    if scheduled.tzinfo is None:
        scheduled = scheduled.replace(tzinfo=UTC)
    ready_from = scheduled + timedelta(minutes=delayed_by)
    day = ready_from.astimezone(zone).date()

    for candidate in later.get((flight.origin_icao, flight.destination_icao), []):
        if int(candidate.id) == int(flight.id):
            continue
        departure = candidate.scheduled_departure
        if departure.tzinfo is None:
            departure = departure.replace(tzinfo=UTC)
        if departure >= ready_from and departure.astimezone(zone).date() == day:
            return False
    return True


async def run_passenger_impact(
    *,
    session: AsyncSession,
    target_refs: list[str],
    evidence_refs: list[str] | None = None,
    **_ignored: Any,
) -> ServiceResult:
    flight_ids = flight_ids_from(target_refs)
    if not flight_ids:
        return _unavailable(
            "no flight is in scope for a passenger impact assessment",
            evidence_refs=evidence_refs,
        )

    cohort_facts = await _cohort_facts_for(session, flight_ids)
    if not cohort_facts:
        return _unavailable(
            "no booking records exist for the flights in scope, so nobody can be ranked",
            evidence_refs=evidence_refs,
        )
    constraints = await load_business_constraints(session)
    return await PassengerImpactService().execute(
        cohort_facts=cohort_facts, business_constraints=constraints
    )


# ------------------------------------------------------------------------------------ hotel


async def _accommodation_demand(session: AsyncSession, flight_ids: set[int]) -> int:
    """Passengers who need a room: those with no onward departure left today.

    Read from the same cohort facts the ranking uses, so the number of rooms requested and the
    list of people they are for cannot disagree.
    """
    facts = await _cohort_facts_for(session, flight_ids)
    return sum(1 for item in facts if item.no_onward_option_today)


async def _hotel_inputs(
    session: AsyncSession, flight_ids: set[int]
) -> tuple[list[Any], int, str | None]:
    airport_icao = await _airport_for_flights(session, flight_ids)
    if airport_icao is None:
        return [], 0, None
    options = await load_hotel_options(session, airport_icao=airport_icao)
    return options, await _accommodation_demand(session, flight_ids), airport_icao


async def _airport_for_flights(session: AsyncSession, flight_ids: set[int]) -> str | None:
    """The airport passengers are stranded at.

    Read from the **incident group** when there is one, because the group declares the airport the
    disruption is at and that is the only reliable answer.

    The version this replaced counted origin and destination appearances across the flights in
    scope and took the maximum. On a single flight that always ties — 6E 2134 is VOBL to VIDP, one
    each — and the tie-break silently picked VIDP. The hotel search then looked for rooms in Delhi,
    found none, and reported "0 properties within the rate cap", which is indistinguishable from
    every hotel being full. A wrong airport that produces an empty result is the worst kind of
    wrong: it looks like a finding.

    Falls back to the flight's origin for an ungrouped incident: a stranded departure leaves its
    passengers where it was due out from.
    """
    from app.models.workflow import Incident, IncidentGroup

    grouped = (
        await session.execute(
            select(IncidentGroup.airport_icao)
            .join(Incident, Incident.group_id == IncidentGroup.id)
            .where(Incident.flight_id.in_(flight_ids))
            .limit(1)
        )
    ).first()
    if grouped and grouped[0]:
        return str(grouped[0])

    rows = (await session.execute(select(Flight).where(Flight.id.in_(flight_ids)))).scalars().all()
    return rows[0].origin_icao if rows else None


async def run_hotel_search(
    *,
    session: AsyncSession,
    target_refs: list[str],
    evidence_refs: list[str] | None = None,
    **_ignored: Any,
) -> ServiceResult:
    flight_ids = flight_ids_from(target_refs)
    if not flight_ids:
        return _unavailable("no flight is in scope for a hotel search", evidence_refs=evidence_refs)

    options, passengers, airport_icao = await _hotel_inputs(session, flight_ids)
    if airport_icao is None:
        return _unavailable(
            "the flights in scope could not be resolved, so no airport can be searched",
            evidence_refs=evidence_refs,
        )
    constraints = await load_business_constraints(session)
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
    """Allocate rooms and record the holds.

    The holds are written **after** the allocation succeeds or partially succeeds, and they are
    what makes the next search see less inventory. Availability is never a mutated counter, so
    two allocations cannot both spend the same room.
    """
    flight_ids = flight_ids_from(target_refs)
    if not flight_ids:
        return _unavailable(
            "no flight is in scope for a hotel allocation", evidence_refs=evidence_refs
        )

    options, passengers, airport_icao = await _hotel_inputs(session, flight_ids)
    if airport_icao is None:
        return _unavailable(
            "the flights in scope could not be resolved, so no rooms can be held",
            evidence_refs=evidence_refs,
        )
    constraints = await load_business_constraints(session)
    result = await HotelAllocationService().execute(
        hotel_options=options,
        passengers=passengers,
        business_constraints=constraints,
    )

    # `needs_human` here is a *partial* allocation, not a refusal: rooms were secured and the
    # holds must be recorded, or the shortfall would be reported against inventory that still
    # looked free. The orchestrator attaches the action id afterwards.
    allocations = result.payload.get("allocations") or []
    if allocations:
        await _record_holds(session, result=result, flight_ids=flight_ids)
    return result


async def _record_holds(
    session: AsyncSession, *, result: ServiceResult, flight_ids: set[int]
) -> None:
    from app.models.cascade import HotelInventoryHold

    group_id = await _group_id_for_flights(session, flight_ids)
    for allocation in result.payload.get("allocations") or []:
        session.add(
            HotelInventoryHold(
                action_id=None,
                hotel_id=int(allocation["hotel_id"]),
                incident_group_id=group_id,
                rooms=int(allocation["rooms"]),
                is_simulated=True,
            )
        )
    await session.flush()


async def _group_id_for_flights(session: AsyncSession, flight_ids: set[int]) -> int | None:
    from app.models.workflow import Incident

    row = (
        await session.execute(
            select(Incident.group_id)
            .where(Incident.flight_id.in_(flight_ids), Incident.group_id.is_not(None))
            .limit(1)
        )
    ).first()
    return int(row[0]) if row and row[0] is not None else None


# ------------------------------------------------------------------------------- registry

#: Action -> adapter, for every Stream C service whose `execute()` is implemented.
#:
#: The services still raising NotImplementedError are deliberately absent, so their actions keep
#: producing the explicit SERVICE_NOT_IMPLEMENTED refusal rather than a silent no-op. Adding one
#: here is the whole integration step.
#:
#: `find_hotel_options` and `reserve_hotel_block` are separate entries on purpose. Search is a
#: read that commits nothing; allocation takes rooms off the market. Collapsing them into one
#: adapter would make looking at options indistinguishable from spending them.
STAGE2_ADAPTERS: dict[ActionType, Any] = {
    ActionType.check_connections: run_connection,
    ActionType.assess_crew_impact: run_crew_impact,
    ActionType.notify_passengers: run_communication,
    ActionType.prepare_notifications: run_communication,
    ActionType.find_hotel_options: run_hotel_search,
    ActionType.reserve_hotel_block: run_hotel_allocation,
}


def register_stage2_services() -> list[str]:
    """Bind every implemented service. Returns the action types now dispatchable."""
    for action, adapter in STAGE2_ADAPTERS.items():
        dispatch.register(action, adapter)
    registered = sorted(action.value for action in STAGE2_ADAPTERS)
    log.info("services_registered", actions=registered, count=len(registered))
    return registered
