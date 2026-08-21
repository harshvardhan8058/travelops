"""Load service inputs from the database.

Services are pure: they take value objects and return a `ServiceResult`, with no session and
no I/O. That is what makes them trivially testable and what keeps the no-LLM boundary a thin
surface. This module is the adapter between the schema and those value objects, so Stream A's
orchestrator can do:

    weather, runways, ruleset = await load_delay_risk_inputs(session, "VOBL")
    result = await DelayRiskService().execute(
        weather=weather, runways=runways, ruleset=ruleset
    )

Owner: Stream C.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.crew import Pairing, PairingLeg
from app.models.policy import BusinessConstraint
from app.models.reference import (
    Booking,
    BookingSegment,
    Flight,
    Passenger,
    Runway,
    WeatherObservation,
)
from app.services.connection import Itinerary, ItinerarySegment, SegmentFlight
from app.services.crew_impact import RosterLeg, RosterPairing, ScheduledFlight
from app.services.delay_risk import (
    DelayRiskRuleset,
    RunwayOption,
    WeatherInput,
    ruleset_from_constraints,
)


def _utc(value: datetime | None) -> datetime | None:
    """Force a database timestamp to aware UTC.

    Postgres returns aware datetimes for `TIMESTAMPTZ`; SQLite returns naive ones for the
    same column. Without this, comparing a stored time against a scenario clock raises
    "can't subtract offset-naive and offset-aware datetimes" on one engine and silently
    works on the other. Storage is always UTC, so attaching the zone here is a correction,
    not an assumption.
    """
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def load_business_constraints(session: AsyncSession) -> list[dict[str, Any]]:
    result = await session.execute(select(BusinessConstraint))
    return [
        {
            "service": row.service,
            "constraint_key": row.constraint_key,
            "constraint_value": row.constraint_value,
            "is_hard": row.is_hard,
            "version": row.version,
        }
        for row in result.scalars()
    ]


def _flight_delay_minutes(flight: Flight) -> int:
    """Delay derived from the estimated departure the simulator wrote.

    Reading it rather than storing a separate `delay_minutes` column keeps one source of
    truth for flight state, per docs/11.
    """
    if flight.estimated_departure is None:
        return 0
    estimated = _utc(flight.estimated_departure)
    scheduled = _utc(flight.scheduled_departure)
    assert estimated is not None and scheduled is not None
    return max(0, int((estimated - scheduled).total_seconds() // 60))


async def load_delay_risk_inputs(
    session: AsyncSession,
    airport_icao: str,
    *,
    as_of: datetime | None = None,
) -> tuple[WeatherInput, list[RunwayOption], DelayRiskRuleset]:
    """The operative actual observation, the airport's runways, and the seeded ruleset.

    `is_forecast = false` is filtered explicitly: scoring a TAF as though it were a METAR is
    the leakage bug `docs/11-data-model.md` names, and the column exists to prevent it.

    **`as_of` matters more than it looks.** The archived AWC observations carry their own true
    timestamps, which are later than the scenario's. Selecting the plain latest row therefore
    returned the clear-weather archive instead of the storm and scored the incident 0/100 — a
    bug found by running the seeded chain rather than by reading it. The correct question is
    "the most recent observation *as of the moment being assessed*", so the caller passes the
    scenario clock and real archived rows are never back-dated to fit.
    """
    conditions = [
        WeatherObservation.airport_icao == airport_icao,
        WeatherObservation.is_forecast.is_(False),
    ]
    if as_of is not None:
        conditions.append(WeatherObservation.observed_at <= as_of)

    observation = (
        await session.execute(
            select(WeatherObservation)
            .where(*conditions)
            .order_by(WeatherObservation.observed_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if observation is None:
        suffix = f" as of {as_of.isoformat()}" if as_of is not None else ""
        raise LookupError(f"no actual weather observation stored for {airport_icao}{suffix}")

    observed_at = _utc(observation.observed_at)
    assert observed_at is not None

    age_minutes: int | None = None
    if as_of is not None:
        age_minutes = max(0, int((as_of - observed_at).total_seconds() // 60))

    weather = WeatherInput(
        airport_icao=observation.airport_icao,
        wind_speed_kt=observation.wind_speed_kt,
        wind_direction_deg=observation.wind_direction_deg,
        visibility_m=observation.visibility_m,
        ceiling_ft=observation.ceiling_ft,
        precipitation=observation.precipitation,
        observation_age_minutes=age_minutes,
        observed_at=observed_at.isoformat(),
        source_ref=observation.source_ref,
        provenance_kind=str(observation.provenance_kind),
    )

    runway_rows = (
        await session.execute(
            select(Runway).where(Runway.airport_icao == airport_icao).order_by(Runway.designator)
        )
    ).scalars()
    runways = [
        RunwayOption(
            designator=row.designator,
            heading_degrees_true=row.heading_degrees_true,
            heading_source=row.heading_source,
            is_active=row.is_active,
        )
        for row in runway_rows
    ]

    ruleset = ruleset_from_constraints(await load_business_constraints(session))
    return weather, runways, ruleset


async def load_connection_inputs(
    session: AsyncSession, affected_flight_ids: set[int]
) -> tuple[list[Itinerary], dict[int, SegmentFlight]]:
    """Itineraries that touch the affected flights, plus every flight they reference.

    Scoped to bookings with a segment on an affected flight, so the walk does not read the
    whole booking table to find the ones that matter.
    """
    booking_ids = (
        (
            await session.execute(
                select(BookingSegment.booking_id)
                .where(BookingSegment.flight_id.in_(affected_flight_ids))
                .distinct()
            )
        )
        .scalars()
        .all()
    )

    if not booking_ids:
        return [], {}

    rows = (
        await session.execute(
            select(Booking, Passenger, BookingSegment)
            .join(Passenger, Passenger.id == Booking.passenger_id)
            .join(BookingSegment, BookingSegment.booking_id == Booking.id)
            .where(Booking.id.in_(booking_ids))
            .order_by(Booking.id, BookingSegment.segment_order)
        )
    ).all()

    by_booking: dict[int, dict[str, Any]] = {}
    flight_ids: set[int] = set()
    for booking, passenger, segment in rows:
        entry = by_booking.setdefault(
            booking.id,
            {
                "booking": booking,
                "passenger": passenger,
                "segments": [],
            },
        )
        entry["segments"].append(segment)
        flight_ids.add(segment.flight_id)

    itineraries = [
        Itinerary(
            booking_id=booking_id,
            pnr=entry["booking"].pnr,
            passenger_id=entry["passenger"].id,
            passenger_reference=entry["passenger"].reference,
            tier=entry["passenger"].tier,
            has_special_needs=entry["passenger"].has_special_needs,
            segments=tuple(
                ItinerarySegment(
                    segment_id=segment.id,
                    segment_order=segment.segment_order,
                    flight_id=segment.flight_id,
                )
                for segment in entry["segments"]
            ),
        )
        for booking_id, entry in sorted(by_booking.items())
    ]

    flight_rows = (await session.execute(select(Flight).where(Flight.id.in_(flight_ids)))).scalars()
    flights = {
        row.id: SegmentFlight(
            flight_id=row.id,
            flight_number=row.flight_number,
            origin_icao=row.origin_icao,
            destination_icao=row.destination_icao,
            scheduled_departure=_utc(row.scheduled_departure),
            scheduled_arrival=_utc(row.scheduled_arrival),
            delay_minutes=_flight_delay_minutes(row),
        )
        for row in flight_rows
    }
    return itineraries, flights


async def load_crew_impact_inputs(
    session: AsyncSession, affected_flight_ids: set[int]
) -> tuple[list[ScheduledFlight], list[RosterPairing], dict[int, ScheduledFlight]]:
    """The affected flights, every pairing that touches them, and all their legs' flights."""
    pairing_ids = (
        (
            await session.execute(
                select(PairingLeg.pairing_id)
                .where(PairingLeg.flight_id.in_(affected_flight_ids))
                .distinct()
            )
        )
        .scalars()
        .all()
    )

    pairing_rows = (
        (
            await session.execute(
                select(Pairing).where(Pairing.id.in_(pairing_ids)).order_by(Pairing.reference)
            )
        )
        .scalars()
        .all()
    )

    leg_rows = (
        (
            await session.execute(
                select(PairingLeg)
                .where(PairingLeg.pairing_id.in_(pairing_ids))
                .order_by(PairingLeg.pairing_id, PairingLeg.leg_order)
            )
        )
        .scalars()
        .all()
    )

    legs_by_pairing: dict[int, list[RosterLeg]] = {}
    flight_ids: set[int] = set(affected_flight_ids)
    for leg in leg_rows:
        legs_by_pairing.setdefault(leg.pairing_id, []).append(
            RosterLeg(
                leg_id=leg.id,
                leg_order=leg.leg_order,
                flight_id=leg.flight_id,
                role=leg.role,
                min_connection_minutes=leg.min_connection_minutes,
            )
        )
        flight_ids.add(leg.flight_id)

    pairings = [
        RosterPairing(
            pairing_id=row.id,
            reference=row.reference,
            base_icao=row.base_icao,
            legs=tuple(legs_by_pairing.get(row.id, [])),
        )
        for row in pairing_rows
    ]

    flight_rows = (await session.execute(select(Flight).where(Flight.id.in_(flight_ids)))).scalars()
    flights = {
        row.id: ScheduledFlight(
            flight_id=row.id,
            flight_number=row.flight_number,
            origin_icao=row.origin_icao,
            destination_icao=row.destination_icao,
            scheduled_departure=_utc(row.scheduled_departure),
            scheduled_arrival=_utc(row.scheduled_arrival),
            delay_minutes=_flight_delay_minutes(row),
        )
        for row in flight_rows
    }

    affected = [flights[flight_id] for flight_id in sorted(affected_flight_ids)]
    return affected, pairings, flights


#: Forward walk over `pairing_leg`, in SQL.
#:
#: The Python attribution in `app.services.crew_impact` is the authority on *why* each pairing
#: is at risk. This exists so the *count* can be verified independently of that code, which is
#: what `docs/22-crew-pairing-model.md` promises: "a recursive SQL query over pairing_leg — no
#: graph database required".
_AFFECTED_PAIRINGS_SQL = """
WITH RECURSIVE touched AS (
    SELECT leg.pairing_id, leg.id AS leg_id, leg.leg_order, leg.flight_id, 0 AS depth
    FROM pairing_leg AS leg
    WHERE leg.flight_id = ANY(:flight_ids)

    UNION ALL

    SELECT onward.pairing_id, onward.id, onward.leg_order, onward.flight_id, walked.depth + 1
    FROM touched AS walked
    JOIN pairing_leg AS onward
      ON onward.pairing_id = walked.pairing_id
     AND onward.leg_order = walked.leg_order + 1
)
SELECT pairing.reference,
       pairing.base_icao,
       COUNT(DISTINCT touched.leg_id) AS legs_reached,
       MAX(touched.depth) AS forward_depth
FROM touched
JOIN pairing ON pairing.id = touched.pairing_id
GROUP BY pairing.reference, pairing.base_icao
ORDER BY pairing.reference
"""


async def affected_pairings_recursive(
    session: AsyncSession, affected_flight_ids: set[int]
) -> list[dict[str, Any]]:
    """Pairings reachable from the affected flights by walking legs forward.

    Postgres only — it uses `= ANY(:flight_ids)`. Callers on another dialect should use
    `load_crew_impact_inputs` and the service instead.
    """
    result = await session.execute(
        text(_AFFECTED_PAIRINGS_SQL), {"flight_ids": sorted(affected_flight_ids)}
    )
    return [
        {
            "reference": row.reference,
            "base_icao": row.base_icao,
            "legs_reached": int(row.legs_reached),
            "forward_depth": int(row.forward_depth),
        }
        for row in result
    ]
