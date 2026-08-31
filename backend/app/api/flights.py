"""The flight board, from the database rather than a fixture.

This replaces the Wave-0 fixture route by exactly the migration its own module documented:
"Delete your endpoint from this module, implement it in your own router, and register that
router in `app/api/__init__.py`. The response shape must stay identical."

## Why it had to move

The Scenario Builder reads this board to resolve a designator into the `flight_id` and
`delay_minutes` it sends to `POST /scenarios`, and that endpoint validates both against the real
`flight` table. While the board was a fixture the two could not agree, and on the committed
dataset they did not:

* `UK 864` (id 4) is not in the database at all, so submitting it answered `404`;
* `AI 503` (id 3) was published with `delay_minutes: 0` while the database derives `65`, so
  submitting it answered `422`;
* the board offered four flights out of the forty-two that exist, so three of the four shipped
  scenario templates could not resolve their flights at all.

Two of four selectable flights were therefore unsubmittable, and the failure surfaced as a
validation error against the operator's own selection — the worst possible shape, because the
input looked legitimate and the rejection looked like a bug in the Scenario API.

`delay_minutes` here is computed by `_flight_delay_minutes`, which is the **same function**
`POST /scenarios` validates against. The board and the validator cannot drift, because there is
only one derivation.

## What is not invented

`risk_index`, `risk_level` and `connections_at_risk` are results of work the system may not have
done yet, so they are `null` until it has:

* risk comes from a persisted `Prediction`. No prediction means no risk score — not zero, which
  would assert "low risk" about a flight nothing has assessed.
* `connections_at_risk` distinguishes two different facts. `null` means no connection check has
  run for this flight. `0` means one has, and found none. Collapsing them would turn "we have not
  looked" into "there is nothing there", which is the reading that gets people hurt.
* `passengers` is a real count over `booking_segment`, so `0` genuinely means no bookings.

The `network` block reports only airports that actually have an observation, and carries that
observation's own provenance — so a live AWC reading shows `real` / `awc` while a seeded one shows
`fixture`, and the console can tell them apart without inferring anything.

Owner: Stream A.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.scenario_queries import _flight_delay_minutes
from app.db.session import get_session
from app.models.enums import ActionStatus, ActionType, IncidentState
from app.models.reference import Airport, BookingSegment, Flight, WeatherObservation
from app.models.workflow import Action, Incident, Prediction
from app.models.workflow import PlanTask as PlanTaskRow
from app.schemas.provenance import Provenance, ProvenanceKind

router = APIRouter(tags=["flights"])


class AirportConditionsOut(BaseModel):
    """Current conditions at one airport, as recorded."""

    model_config = ConfigDict(extra="forbid")

    airport_icao: str
    iata: str
    city: str
    wind_speed_kt: int | None = None
    wind_direction_deg: int | None = None
    visibility_m: int | None = None
    ceiling_ft: int | None = None
    precipitation: str | None = None
    #: Null until something has actually been scored at this airport.
    risk_index: int | None = None
    risk_level: str | None = None
    observation_age_minutes: int | None = None
    provenance: Provenance


class FlightRowOut(BaseModel):
    """One flight, with only the figures the system genuinely holds."""

    model_config = ConfigDict(extra="forbid")

    id: int
    flight_number: str
    airline_code: str
    origin_icao: str
    destination_icao: str
    scheduled_departure: datetime
    estimated_departure: datetime | None = None
    #: Derived by the same function `POST /scenarios` validates against.
    delay_minutes: int
    block_time_minutes: int
    status: str
    #: Null until a `Prediction` exists for this flight.
    risk_index: int | None = None
    risk_level: str | None = None
    #: A real count over `booking_segment`, so 0 means no bookings.
    passengers: int
    #: Null when no connection check has run; 0 when one has and found none.
    connections_at_risk: int | None = None
    incident_reference: str | None = None
    provenance: Provenance


class FlightBoardResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    network: list[AirportConditionsOut]
    flights: list[FlightRowOut]


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite returns naive timestamps for `TIMESTAMPTZ`; Postgres returns aware ones."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def _passengers_by_flight(session: AsyncSession) -> dict[int, int]:
    """Distinct bookings touching each flight. A real count, never an estimate."""
    rows = (
        await session.execute(
            select(
                BookingSegment.flight_id,
                func.count(func.distinct(BookingSegment.booking_id)),
            ).group_by(BookingSegment.flight_id)
        )
    ).all()
    return {int(flight_id): int(count) for flight_id, count in rows}


async def _latest_prediction_by_flight(session: AsyncSession) -> dict[int, Prediction]:
    rows = (await session.execute(select(Prediction).order_by(Prediction.id))).scalars()
    return {int(row.flight_id): row for row in rows}


async def _latest_prediction_by_airport(session: AsyncSession) -> dict[str, Prediction]:
    rows = (await session.execute(select(Prediction).order_by(Prediction.id))).scalars()
    return {str(row.airport_icao): row for row in rows}


async def _incident_reference_by_flight(session: AsyncSession) -> dict[int, str]:
    """The incident an operator would follow from this flight.

    An active incident wins over a closed one; among equals the most recent. Ordering
    ascending and overwriting leaves the highest id, then active states overwrite terminal ones.
    """
    rows = (
        await session.execute(
            select(Incident.flight_id, Incident.reference, Incident.state).order_by(Incident.id)
        )
    ).all()
    chosen: dict[int, tuple[str, bool]] = {}
    terminal = {s.value for s in IncidentState.terminal()}
    for flight_id, reference, state in rows:
        active = str(state) not in terminal
        current = chosen.get(int(flight_id))
        if current is None or active or not current[1]:
            chosen[int(flight_id)] = (str(reference), active)
    return {flight_id: reference for flight_id, (reference, _active) in chosen.items()}


async def _connections_by_flight_number(session: AsyncSession) -> dict[str, int] | None:
    """At-risk counts keyed by flight number, or None when nothing has been assessed.

    Read from the recorded `check_connections` results rather than recomputed, so the board
    reports what the system actually decided. `None` for the whole map means no check has run
    anywhere, which is why an unassessed flight reports `null` instead of `0`.
    """
    rows = (
        await session.execute(
            # The action type lives on the plan task the action executed, not on the action row.
            select(Action.payload)
            .join(PlanTaskRow, PlanTaskRow.id == Action.plan_task_id)
            .where(
                PlanTaskRow.action_type == ActionType.check_connections.value,
                Action.status == ActionStatus.success,
            )
        )
    ).all()
    if not rows:
        return None
    counts: dict[str, int] = {}
    for (payload,) in rows:
        by_flight = (payload or {}).get("at_risk_by_flight") or {}
        if not isinstance(by_flight, dict):
            continue
        for number, count in by_flight.items():
            if isinstance(count, int):
                counts[str(number)] = counts.get(str(number), 0) + count
    return counts


async def _network(session: AsyncSession, *, now: datetime) -> list[AirportConditionsOut]:
    """Airports that actually have an observation, newest observation each.

    Forecasts are excluded for the same reason `load_delay_risk_inputs` excludes them: a TAF is
    not an observation of what is happening, and presenting one as current conditions is the
    leakage the `is_forecast` column exists to prevent.
    """
    observations = (
        await session.execute(
            select(WeatherObservation)
            .where(WeatherObservation.is_forecast.is_(False))
            .order_by(WeatherObservation.observed_at)
        )
    ).scalars()
    latest: dict[str, WeatherObservation] = {}
    for row in observations:
        latest[str(row.airport_icao)] = row
    if not latest:
        return []

    airports = {
        str(row.icao_code): row
        for row in (
            await session.execute(select(Airport).where(Airport.icao_code.in_(list(latest))))
        ).scalars()
    }
    risk = await _latest_prediction_by_airport(session)

    out: list[AirportConditionsOut] = []
    for icao in sorted(latest):
        observation = latest[icao]
        airport = airports.get(icao)
        observed_at = _as_utc(observation.observed_at)
        age = None
        if observed_at is not None:
            age = max(0, int((now - observed_at).total_seconds() // 60))
        prediction = risk.get(icao)
        out.append(
            AirportConditionsOut(
                airport_icao=icao,
                iata=airport.iata_code if airport else "",
                city=airport.city if airport else "",
                wind_speed_kt=observation.wind_speed_kt,
                wind_direction_deg=observation.wind_direction_deg,
                visibility_m=observation.visibility_m,
                ceiling_ft=observation.ceiling_ft,
                precipitation=observation.precipitation,
                risk_index=int(prediction.risk_index) if prediction else None,
                risk_level=str(prediction.risk_level) if prediction else None,
                observation_age_minutes=age,
                provenance=Provenance(
                    kind=ProvenanceKind(str(observation.provenance_kind)),
                    provider=observation.provenance_provider,
                    source_ref=observation.source_ref,
                    observed_at=observed_at,
                ),
            )
        )
    return out


@router.get(
    "/flights",
    response_model=FlightBoardResponse,
    summary="Flight board and network conditions, from persisted state",
)
async def list_flights(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FlightBoardResponse:
    """Every flight the system holds, with the figures it has actually derived.

    Unassessed risk and unassessed connection counts are `null`. That is the point of the
    endpoint: an operator authoring a scenario needs to know which flights are real and what
    their recorded delay is, and needs not to be shown a confident number for work nobody did.
    """
    now = datetime.now(tz=UTC)

    flights = (
        await session.execute(select(Flight).order_by(Flight.scheduled_departure, Flight.id))
    ).scalars()
    passengers = await _passengers_by_flight(session)
    predictions = await _latest_prediction_by_flight(session)
    incidents = await _incident_reference_by_flight(session)
    connections = await _connections_by_flight_number(session)

    rows: list[FlightRowOut] = []
    for flight in flights:
        prediction = predictions.get(flight.id)
        rows.append(
            FlightRowOut(
                id=flight.id,
                flight_number=flight.flight_number,
                airline_code=flight.airline_code,
                origin_icao=flight.origin_icao,
                destination_icao=flight.destination_icao,
                scheduled_departure=_as_utc(flight.scheduled_departure),
                estimated_departure=_as_utc(flight.estimated_departure),
                delay_minutes=_flight_delay_minutes(flight),
                block_time_minutes=flight.block_time_minutes,
                status=flight.status,
                risk_index=int(prediction.risk_index) if prediction else None,
                risk_level=str(prediction.risk_level) if prediction else None,
                passengers=passengers.get(flight.id, 0),
                connections_at_risk=(
                    None if connections is None else connections.get(flight.flight_number, 0)
                ),
                incident_reference=incidents.get(flight.id),
                provenance=Provenance(
                    kind=ProvenanceKind(str(flight.provenance_kind)),
                    provider="generator",
                    source_ref=flight.source_ref,
                ),
            )
        )

    return FlightBoardResponse(network=await _network(session, now=now), flights=rows)
