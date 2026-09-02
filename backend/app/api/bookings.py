"""`GET /bookings/{pnr}` — the trip behind a booking reference, for the passenger view.

The passenger route (`/passenger/:bookingRef`) previously had no way to say which flight, or
flights, a booking reference names. `GET /incident-groups/{ref}/impacts` publishes a priority
ranking keyed on `pnr`, and that is a real and useful fact, but it carries no flight number, no
route, no scheduled time and no delay — a passenger cannot be told what happened to their trip
from a ranking record alone. So the passenger screen either had to invent trip details (which
this repository does not do) or omit the trip entirely, and "Trip / flight" is the first thing a
passenger-facing screen is required to show.

This endpoint answers that, and nothing more. It is a read over `booking` and `booking_segment`,
in the same shape and with the same derivations `GET /flights` already uses for delay and
incident linkage — `_flight_delay_minutes` is the one function both call, so the two can never
disagree about how many minutes a flight is delayed. It does not touch the orchestrator, the
workflow lifecycle, or any write path: a booking has no state machine of its own, only the
segments' flights and the incidents open against them, both of which are read here exactly as
`GET /flights` reads them for the operator board.

A booking commonly carries more than one segment — `K4X8YR` in the seeded dataset connects VOBL
-> VABB -> VIDP — so `segments` is a list, ordered by `segment_order`, and a broken connection is
visible as two segments rather than collapsed into one.

Owner: Stream A.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.scenario_queries import _flight_delay_minutes
from app.db.session import get_session
from app.errors import EntityNotFound
from app.models.enums import IncidentState
from app.models.reference import Booking, BookingSegment, Flight, Passenger
from app.models.workflow import Incident
from app.schemas.provenance import Provenance, ProvenanceKind

router = APIRouter(prefix="/bookings", tags=["bookings"])


class BookingSegmentOut(BaseModel):
    """One flight in the trip.

    Delay and incident linkage are derived exactly as `GET /flights` derives them.
    """

    model_config = ConfigDict(extra="forbid")

    segment_order: int
    flight_id: int
    flight_number: str
    origin_icao: str
    destination_icao: str
    scheduled_departure: datetime
    estimated_departure: datetime | None = None
    #: Derived by the same function `GET /flights` and `POST /scenarios` use.
    #: Never a second formula.
    delay_minutes: int
    status: str
    #: The incident an operator would follow from this segment, or `null` if none is open.
    incident_reference: str | None = None
    provenance: Provenance


class BookingLookupResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pnr: str
    #: The same reference `PassengerImpact.passenger_reference` carries, so the priority ranking
    #: and this trip lookup can be correlated without a passenger id ever reaching the client.
    passenger_reference: str
    cabin: str
    #: Ordered by `segment_order`. Rarely a single flight: a broken connection is two segments,
    #: not one collapsed row.
    segments: list[BookingSegmentOut]


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite returns naive timestamps for `TIMESTAMPTZ`; Postgres returns aware ones."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def _incident_reference_for(session: AsyncSession, flight_ids: list[int]) -> dict[int, str]:
    """The incident an operator would follow from each flight, active winning over closed.

    Scoped to the handful of flights in one booking rather than `GET /flights`'s whole-table read
    — the same rule (`_incident_reference_by_flight`), applied to a much smaller set.
    """
    if not flight_ids:
        return {}
    rows = (
        await session.execute(
            select(Incident.flight_id, Incident.reference, Incident.state).where(
                Incident.flight_id.in_(flight_ids)
            )
        )
    ).all()
    terminal = {s.value for s in IncidentState.terminal()}
    chosen: dict[int, tuple[str, bool]] = {}
    for flight_id, reference, state in sorted(rows, key=lambda row: row[1]):
        active = str(state) not in terminal
        current = chosen.get(int(flight_id))
        if current is None or active or not current[1]:
            chosen[int(flight_id)] = (str(reference), active)
    return {flight_id: reference for flight_id, (reference, _active) in chosen.items()}


@router.get(
    "/{pnr}",
    response_model=BookingLookupResponse,
    summary="The trip behind a booking reference: its flights, in order, with recorded delay",
)
async def get_booking(
    pnr: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BookingLookupResponse:
    normalised = pnr.strip().upper()
    booking = (
        (await session.execute(select(Booking).where(Booking.pnr == normalised))).scalars().first()
    )
    if booking is None:
        raise EntityNotFound(
            "booking not found",
            details={
                "pnr": pnr,
                "resolution": (
                    "No booking carries this reference. Check the reference, or open a booking "
                    "from a disruption's recorded passenger records."
                ),
            },
        )

    passenger = await session.get(Passenger, booking.passenger_id)
    assert passenger is not None  # `passenger_id` is a non-null foreign key.

    segment_rows = (
        await session.execute(
            select(BookingSegment, Flight)
            .join(Flight, Flight.id == BookingSegment.flight_id)
            .where(BookingSegment.booking_id == booking.id)
            .order_by(BookingSegment.segment_order)
        )
    ).all()

    incidents = await _incident_reference_for(
        session, [flight.id for _segment, flight in segment_rows]
    )

    segments = [
        BookingSegmentOut(
            segment_order=segment.segment_order,
            flight_id=flight.id,
            flight_number=flight.flight_number,
            origin_icao=flight.origin_icao,
            destination_icao=flight.destination_icao,
            scheduled_departure=_as_utc(flight.scheduled_departure),
            estimated_departure=_as_utc(flight.estimated_departure),
            delay_minutes=_flight_delay_minutes(flight),
            status=flight.status,
            incident_reference=incidents.get(flight.id),
            provenance=Provenance(
                kind=ProvenanceKind(str(flight.provenance_kind)),
                provider="generator",
                source_ref=flight.source_ref,
            ),
        )
        for segment, flight in segment_rows
    ]

    return BookingLookupResponse(
        pnr=booking.pnr,
        passenger_reference=passenger.reference,
        cabin=booking.cabin,
        segments=segments,
    )
