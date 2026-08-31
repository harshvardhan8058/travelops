"""Map a live/fixture flight-status reading into the domain the services already speak.

The providers speak AviationStack; the services speak `SegmentFlight` (see
`app.services.connection`). This module is the one place that translation happens, so the
connection and recovery services never learn a vendor's field names.

It is deliberately a thin, pure mapping with a fail-safe result type — not a service that
decides anything. Nothing here authorises an action: a status feeds the connection walk, and
the orchestrator still asks the Decision Assurance Gate before any action is dispatched. A
failed or stale lookup produces `provenance_kind=unavailable` and no `SegmentFlight`, which is
what keeps a missing status from reading as an on-time flight.

Owner: Stream C.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ProvenanceKind
from app.providers.base import ProviderError
from app.services.connection import SegmentFlight


class FlightStatusIngestResult(BaseModel):
    """Outcome of mapping one provider status into a `SegmentFlight`.

    `segment` is populated only when the status was usable. On any failure it is None and the
    reason plus provenance say why, so a caller can never mistake absence for an on-time,
    zero-delay flight.
    """

    model_config = ConfigDict(extra="forbid")

    flight_id: int
    segment: SegmentFlight | None = None
    status: str | None = None
    cancelled: bool = False
    delay_minutes: int | None = None
    source_ref: str | None = None
    #: real | simulated | fixture | unavailable
    provenance_kind: str = ProvenanceKind.unavailable.value
    is_stale: bool = False
    evidence_refs: list[str] = Field(default_factory=list)
    reason: str | None = None

    @property
    def usable(self) -> bool:
        return self.segment is not None


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def segment_from_status(
    normalised: dict[str, Any],
    *,
    flight_id: int,
) -> FlightStatusIngestResult:
    """Turn a provider's normalised status dict into a `FlightStatusIngestResult`.

    Fails safe: if the status lacks the endpoints or scheduled times a `SegmentFlight` needs,
    or if the flight is cancelled (a cancellation is not a delay and must not be modelled as
    one), the result carries no segment and explains why. The delay applied to the segment is
    the departure delay, exactly as `SegmentFlight` expects.
    """
    provenance = normalised.get("provenance") or {}
    provenance_kind = str(provenance.get("kind") or ProvenanceKind.unavailable.value)
    source_ref = provenance.get("source_ref")
    is_stale = bool(provenance.get("is_stale", False))
    status = normalised.get("status")
    cancelled = bool(normalised.get("cancelled", False))
    delay_minutes = normalised.get("delay_minutes")

    evidence_refs: list[str] = [f"flight:{flight_id}"]
    if source_ref:
        evidence_refs.append(f"flight_status:{source_ref}")

    base = {
        "flight_id": flight_id,
        "status": status,
        "cancelled": cancelled,
        "delay_minutes": delay_minutes,
        "source_ref": source_ref,
        "provenance_kind": provenance_kind,
        "is_stale": is_stale,
        "evidence_refs": evidence_refs,
    }

    if cancelled:
        # A cancellation is a different event from a delay. Modelling it as a huge delay would
        # let the connection walk silently "recover" it if an onward flight were later still.
        return FlightStatusIngestResult(
            **base,
            reason=(
                f"flight_id={flight_id} is cancelled; a cancellation is handled by recovery, "
                "not modelled as a delayed segment"
            ),
        )

    flight_number = normalised.get("flight_number")
    origin = normalised.get("origin_icao")
    destination = normalised.get("destination_icao")
    scheduled_departure = _as_datetime(normalised.get("scheduled_departure"))
    scheduled_arrival = _as_datetime(normalised.get("scheduled_arrival"))

    missing = [
        name
        for name, value in (
            ("flight_number", flight_number),
            ("origin_icao", origin),
            ("destination_icao", destination),
            ("scheduled_departure", scheduled_departure),
            ("scheduled_arrival", scheduled_arrival),
        )
        if not value
    ]
    if missing:
        return FlightStatusIngestResult(
            **base,
            reason=(
                f"flight_id={flight_id} status is missing {', '.join(missing)}; "
                "cannot build a segment without inventing schedule data"
            ),
        )

    segment = SegmentFlight(
        flight_id=flight_id,
        flight_number=str(flight_number),
        origin_icao=str(origin),
        destination_icao=str(destination),
        scheduled_departure=scheduled_departure,
        scheduled_arrival=scheduled_arrival,
        delay_minutes=int(delay_minutes or 0),
    )
    return FlightStatusIngestResult(**base, segment=segment)


async def ingest_flight_status(
    provider: Any,
    *,
    flight_id: int,
) -> FlightStatusIngestResult:
    """Fetch one flight's status through a provider and map it, catching provider failures.

    The provider raises `ProviderError` on any failure — timeout, rate limit, missing flight,
    forbidden. This turns that into a fail-safe result with `provenance_kind=unavailable` and a
    reason, so the caller gets an explicit "no data" it can route to `needs_human` rather than
    an exception it might swallow into a default.
    """
    try:
        normalised = await provider.get_status(flight_id)
    except ProviderError as exc:
        return FlightStatusIngestResult(
            flight_id=flight_id,
            provenance_kind=ProvenanceKind.unavailable.value,
            evidence_refs=[f"flight:{flight_id}"],
            reason=f"{exc.kind.value}: {exc.message}",
        )
    return segment_from_status(normalised, flight_id=flight_id)
