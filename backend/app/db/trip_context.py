"""Assemble the policy engine's trip context from records — STREAM C's DB layer.

`app.policy.entitlements.calculate` needs a trip context: the event, the itinerary, the flight,
the fare, the passenger and any cause evidence. The orchestrator deliberately does not build one
(`engine._policy_facts` reads only what a plan supplied), because assembling facts is a database
concern and the orchestrator has no business inventing them.

So this module builds it, under one rule: **every fact is read from a row, or it is absent.**

Absence matters more than presence here. The policy engine is written to return `needs_human`
naming a missing fact, and `app/policy/requirements.py` derives what the gate must demand from
the pack itself. Both of those protections are defeated by a loader that defaults a fare to zero
or a notice period to "plenty" — the pack would then evaluate cleanly against facts nobody
recorded, and produce a figure a passenger could rely on. So a column that is NULL is simply not
placed in the dictionary, and the engine is allowed to refuse.

Owner: Stream C (loaders) / Stream B (what the facts mean).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.reference import Airport, Booking, BookingSegment, Flight

#: Passenger-facing local time. Storage stays UTC; the pack's night-window rule is written
#: against local scheduled departure, so the conversion has to happen somewhere and it happens
#: once, here.
DISPLAY_TIMEZONE = "Asia/Kolkata"

#: Domestic when both ends are Indian airports. Read from the airport rows rather than assumed
#: from the ICAO prefix, because `V` also covers Sri Lanka and the Maldives.
INDIA_COUNTRY = "IN"


def _prune(value: Any) -> Any:
    """Drop keys whose value is None, recursively.

    This is the whole safety property of the module in one function: an absent fact must not
    reach the engine as an explicit null, because a null compares differently from a missing
    key in `_lookup` and would let a condition evaluate rather than stay undetermined.
    """
    if isinstance(value, dict):
        pruned = {key: _prune(item) for key, item in value.items() if item is not None}
        return {key: item for key, item in pruned.items() if item not in (None, {}, [])}
    return value


def _local(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    return aware.astimezone(ZoneInfo(DISPLAY_TIMEZONE))


def _delay_minutes(flight: Flight) -> int | None:
    """Delay derived from scheduled versus estimated departure, or None.

    `flight` carries no `delay_minutes` column; the delay is the difference between the two
    timestamps. Returns None when either is absent rather than zero: "not delayed" and "delay
    not recorded" are different facts, and only one of them means a passenger is owed nothing.
    """
    scheduled = flight.scheduled_departure
    estimated = getattr(flight, "estimated_departure", None)
    if scheduled is None or estimated is None:
        return None
    scheduled = scheduled if scheduled.tzinfo else scheduled.replace(tzinfo=UTC)
    estimated = estimated if estimated.tzinfo else estimated.replace(tzinfo=UTC)
    return int((estimated - scheduled).total_seconds() // 60)


async def load_trip_context(
    session: AsyncSession,
    flight_ids: set[int] | list[int],
    *,
    booking_id: int | None = None,
) -> dict[str, Any]:
    """Build the trip context for the first flight in scope.

    One flight, not an aggregate. An entitlement is a per-passenger, per-itinerary judgement, and
    a context averaged over eight flights would produce a figure that applies to nobody. The
    caller scopes the task to the flight it is acting on.

    `cause_evidence` is deliberately **not** populated from `trigger_type`. Inferring "external
    to carrier, unavoidable despite reasonable measures" from the word `weather` would be this
    system asserting a legal exemption it has no evidence for — and it is exactly the inference
    `app/services/compensation.py` promises never to make. An exemption has to be evidenced by a
    recorded assessment, so when none exists the pack evaluates without it.
    """
    ids = sorted(flight_ids)
    if not ids:
        return {}

    flight = await session.get(Flight, ids[0])
    if flight is None:
        return {}

    origin = await session.get(Airport, flight.origin_icao)
    destination = await session.get(Airport, flight.destination_icao)
    scheduled_local = _local(flight.scheduled_departure)
    delay = _delay_minutes(flight)

    booking: Booking | None = None
    if booking_id is not None:
        booking = await session.get(Booking, booking_id)
    else:
        booking = (
            (
                await session.execute(
                    select(Booking)
                    .join(BookingSegment, BookingSegment.booking_id == Booking.id)
                    .where(BookingSegment.flight_id == flight.id)
                    .order_by(Booking.id)
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )

    origin_country = origin.country if origin else None
    destination_country = destination.country if destination else None
    is_domestic = (
        origin_country == INDIA_COUNTRY and destination_country == INDIA_COUNTRY
        if origin_country and destination_country
        else None
    )

    context: dict[str, Any] = {
        "event": {
            "type": "delay",
            "delay_minutes": delay,
            # The pack reads both. They are the same recorded figure here because nothing in
            # this dataset forecasts a delay separately from observing it, and inventing a
            # divergence would be inventing a forecast.
            "expected_delay_minutes": delay,
            "travel_date": scheduled_local.date().isoformat() if scheduled_local else None,
        },
        "itinerary": {
            "is_domestic": is_domestic,
            "origin_country": origin_country,
            "destination_country": destination_country,
            "scheduled_departure_local": scheduled_local.isoformat() if scheduled_local else None,
        },
        "flight": {
            "block_time_minutes": getattr(flight, "block_time_minutes", None),
            "scheduled_departure_local_time": (
                scheduled_local.strftime("%H:%M") if scheduled_local else None
            ),
            "scheduled_departure_local": scheduled_local.isoformat() if scheduled_local else None,
        },
        "operating_carrier": {
            "id": getattr(flight, "airline_code", None),
        },
        "fare": {
            # Nullable on `booking`, and left absent when null. The charter's cancellation and
            # denied-boarding formulas read these, so a default would fabricate a cash figure.
            "one_way_basic_fare_inr": booking.one_way_basic_fare_inr if booking else None,
            "airline_fuel_charge_inr": booking.airline_fuel_charge_inr if booking else None,
        },
        "passenger": {
            # `checked_in_on_time` is not recorded by this schema, so it is not asserted. The
            # pack treats it as undetermined, which is the truth.
            "contact_info_provided_at_booking": (
                bool(booking.contact_info_provided_at_booking) if booking else None
            ),
        },
    }

    return _prune(context)
