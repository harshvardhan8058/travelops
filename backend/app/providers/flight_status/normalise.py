"""The boundary that turns one live flight-status payload into domain values.

This is the flight-status twin of `app.providers.weather.normalise`, and it exists for the
same reason: every ambiguous field is resolved **once, here**, so no downstream service has to
guess. The dangerous fields are the timestamps and the delay, because a wrong answer here is a
plausible number rather than an error — a flight reported on time when it is two hours late is
indistinguishable downstream from a flight that is genuinely on time.

Canonical shapes produced here:

* delay is **whole minutes, non-negative**, derived from the schedule when the vendor omits it
  rather than trusted blindly;
* every timestamp is timezone-aware UTC;
* the identity fields (`flight_id`, `flight_number`, ICAO endpoints) are carried through so the
  result can be reconciled against the booking the service already holds.

Shared by the live and fixture providers on purpose. If the two normalised independently, a
contract test passing in both modes would prove nothing.

Owner: Stream C.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.providers.base import ProviderError, ProviderErrorKind

#: AviationStack `flight_status` vocabulary, mapped to the small set the domain cares about.
#: Unknown values are preserved verbatim rather than coerced, so an unexpected state surfaces
#: instead of being silently flattened to "scheduled".
KNOWN_STATUSES: frozenset[str] = frozenset(
    {"scheduled", "active", "landed", "cancelled", "incident", "diverted"}
)


def utc_from_iso(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp to timezone-aware UTC, or None.

    AviationStack emits `2024-03-30T09:55:00+00:00`. A naive timestamp is assumed UTC rather
    than local, because guessing a local zone is how a delay computation silently gains or
    loses hours.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def delay_minutes_from_endpoint(
    endpoint: dict[str, Any] | None,
    *,
    scheduled: datetime | None,
    best_known: datetime | None,
) -> int:
    """The delay in whole non-negative minutes for one endpoint (departure/arrival).

    Two sources, in order of trust:

    1. The vendor's own `delay` field when present. It is already in minutes.
    2. Otherwise the difference between the best-known revised time (actual, else estimated)
       and the scheduled time.

    Never negative: an early departure is not a negative delay for connection purposes, it is
    zero delay. A negative number here would flow into `SegmentFlight.delay_minutes` and make a
    revised arrival earlier than scheduled, which would silently *heal* a broken connection.
    """
    if endpoint is not None:
        raw = endpoint.get("delay")
        if raw is not None:
            try:
                return max(0, int(raw))
            except (TypeError, ValueError):
                pass

    if scheduled is not None and best_known is not None:
        minutes = int((best_known - scheduled).total_seconds() // 60)
        return max(0, minutes)

    return 0


def _endpoint_icao(endpoint: dict[str, Any] | None) -> str | None:
    """Prefer ICAO, fall back to nothing.

    The domain keys airports by ICAO (VOBL, VIDP). IATA is deliberately NOT substituted: a
    silent IATA-for-ICAO swap would produce a valid-looking code that keys nothing, and the
    connection walk would drop the segment rather than raise.
    """
    if not endpoint:
        return None
    icao = endpoint.get("icao")
    return str(icao).upper() if icao else None


def best_known_time(endpoint: dict[str, Any] | None) -> datetime | None:
    """The most authoritative revised time: actual, else estimated, else None.

    Scheduled is deliberately excluded here — the caller passes it separately — so that
    "no revised time is known" stays distinct from "revised time equals scheduled".
    """
    if not endpoint:
        return None
    return utc_from_iso(endpoint.get("actual")) or utc_from_iso(endpoint.get("estimated"))


def normalise_status_row(
    row: dict[str, Any],
    *,
    flight_id: int,
    provider: str,
) -> dict[str, Any]:
    """Turn one AviationStack `flights` row into the canonical flight-status shape.

    The returned dict is the vendor-neutral contract the service layer consumes. It carries
    the scheduled and revised times, the derived delay, the raw status, and the endpoint ICAOs
    — everything needed to build a `SegmentFlight` and to reconcile it against the booking.

    Raises `ProviderError(invalid_response)` when the row cannot yield a usable departure
    schedule, because a status with no schedule cannot produce a delay and a fabricated one
    would be worse than a refusal.
    """
    departure = row.get("departure") if isinstance(row.get("departure"), dict) else None
    arrival = row.get("arrival") if isinstance(row.get("arrival"), dict) else None

    scheduled_departure = utc_from_iso((departure or {}).get("scheduled"))
    if scheduled_departure is None:
        raise ProviderError(
            ProviderErrorKind.invalid_response,
            f"flight-status row for flight_id={flight_id} has no scheduled departure time",
            provider=provider,
        )

    scheduled_arrival = utc_from_iso((arrival or {}).get("scheduled"))

    revised_departure = best_known_time(departure)
    revised_arrival = best_known_time(arrival)

    departure_delay = delay_minutes_from_endpoint(
        departure, scheduled=scheduled_departure, best_known=revised_departure
    )
    arrival_delay = delay_minutes_from_endpoint(
        arrival, scheduled=scheduled_arrival, best_known=revised_arrival
    )

    raw_status = row.get("flight_status")
    status = str(raw_status).lower() if raw_status else "unknown"

    flight_block = row.get("flight") if isinstance(row.get("flight"), dict) else {}
    flight_number = (
        flight_block.get("iata") or flight_block.get("icao") or flight_block.get("number")
    )

    return {
        "flight_id": flight_id,
        "flight_number": str(flight_number) if flight_number else None,
        "status": status,
        "status_is_known": status in KNOWN_STATUSES,
        "cancelled": status == "cancelled",
        "origin_icao": _endpoint_icao(departure),
        "destination_icao": _endpoint_icao(arrival),
        "scheduled_departure": scheduled_departure,
        "scheduled_arrival": scheduled_arrival,
        "revised_departure": revised_departure,
        "revised_arrival": revised_arrival,
        # `delay_minutes` is the departure delay: it is what shifts the whole segment and what
        # `SegmentFlight` applies to both scheduled times. Arrival delay is reported alongside
        # for services that want it, but it never silently becomes the segment delay.
        "delay_minutes": departure_delay,
        "arrival_delay_minutes": arrival_delay,
    }
