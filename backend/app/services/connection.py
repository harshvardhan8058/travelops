"""Connection service — STREAM C.

Identify itineraries whose onward segment is no longer feasible after a delay.

Compare the revised arrival of the delayed segment against the scheduled departure of the
next segment on the same booking, allowing the minimum connection time. Return the exact
booking and segment references so the count is traceable rather than asserted.

Two decisions worth stating, because both are places a count can quietly become wrong:

1. **Feasibility is judged against the SCHEDULED departure of the onward segment**, not its
   revised one. The question a controller needs answered is "does this itinerary still work
   as sold?" If the onward flight is itself delayed the passenger may in fact make it, and
   that is recorded separately as `recovered_by_onward_delay` rather than being silently
   dropped from the count. Dropping it would make the number depend on the order the two
   delays happened to be applied.
2. **A booking is counted once**, on its first broken connection. A four-segment itinerary
   that breaks at segment two is one broken itinerary, not three.

Minimum connection time comes from `business_constraint`, never from a literal.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from itertools import pairwise
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.enums import ActionStatus, ProvenanceKind
from app.services.base import ServiceResult

RULE_VERSION = "connection-v1"

BUSINESS_CONSTRAINT_SERVICE = "connection_service"
BUSINESS_CONSTRAINT_KEY = "minimum_connection_minutes"

#: Fallback only. The seeded `business_constraint` row is the runtime source, and this value
#: is the same one that gets seeded so "no rows" and "seeded rows" agree.
DEFAULT_MINIMUM_CONNECTION_MINUTES = 45


class SegmentFlight(BaseModel):
    """The flight a segment sits on, with whatever delay currently applies."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    flight_id: int
    flight_number: str
    origin_icao: str
    destination_icao: str
    scheduled_departure: datetime
    scheduled_arrival: datetime
    delay_minutes: int = 0

    @property
    def revised_departure(self) -> datetime:
        return self.scheduled_departure + timedelta(minutes=self.delay_minutes)

    @property
    def revised_arrival(self) -> datetime:
        return self.scheduled_arrival + timedelta(minutes=self.delay_minutes)


class ItinerarySegment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    segment_id: int
    segment_order: int
    flight_id: int


class Itinerary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    booking_id: int
    pnr: str
    passenger_id: int
    passenger_reference: str
    tier: str = "standard"
    has_special_needs: bool = False
    segments: tuple[ItinerarySegment, ...]

    @property
    def ordered_segments(self) -> list[ItinerarySegment]:
        return sorted(self.segments, key=lambda segment: segment.segment_order)


class BrokenConnection(BaseModel):
    """One itinerary that no longer works, with everything needed to trace it."""

    model_config = ConfigDict(extra="forbid")

    booking_id: int
    pnr: str
    passenger_id: int
    passenger_reference: str
    tier: str
    has_special_needs: bool

    inbound_segment_id: int
    inbound_flight_id: int
    inbound_flight_number: str
    inbound_scheduled_arrival: datetime
    inbound_revised_arrival: datetime
    inbound_delay_minutes: int

    onward_segment_id: int
    onward_flight_id: int
    onward_flight_number: str
    onward_scheduled_departure: datetime

    connection_airport_icao: str
    minimum_connection_minutes: int
    #: Scheduled turnaround minus the minimum connection, after the delay. Negative.
    shortfall_minutes: int
    #: True when the onward flight is itself delayed enough that the passenger may still
    #: make it. Still counted as broken as sold, but flagged so nobody is re-accommodated
    #: who does not need to be.
    recovered_by_onward_delay: bool


class ConnectionAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    at_risk: list[BrokenConnection]
    connecting_itineraries_examined: int
    single_segment_itineraries: int
    rule_version: str = RULE_VERSION
    minimum_connection_minutes: int

    @property
    def count(self) -> int:
        return len(self.at_risk)


def _minimum_connection_minutes(rows: list[dict[str, Any]] | None) -> int:
    for row in rows or []:
        if (
            row.get("service") == BUSINESS_CONSTRAINT_SERVICE
            and row.get("constraint_key") == BUSINESS_CONSTRAINT_KEY
        ):
            value = row["constraint_value"]
            if isinstance(value, dict):
                return int(value["minutes"])
            return int(value)
    return DEFAULT_MINIMUM_CONNECTION_MINUTES


def find_at_risk_connections(
    *,
    itineraries: list[Itinerary],
    flights: dict[int, SegmentFlight],
    minimum_connection_minutes: int = DEFAULT_MINIMUM_CONNECTION_MINUTES,
    affected_flight_ids: set[int] | None = None,
) -> ConnectionAssessment:
    """Walk each itinerary and report the first connection that no longer works.

    Deterministic: itineraries are processed in booking-id order and each contributes at
    most one result, so the count does not depend on input ordering.
    """
    broken: list[BrokenConnection] = []
    connecting = 0
    single = 0

    for itinerary in sorted(itineraries, key=lambda item: item.booking_id):
        segments = itinerary.ordered_segments
        if len(segments) < 2:
            single += 1
            continue
        connecting += 1

        for inbound_segment, onward_segment in pairwise(segments):
            inbound = flights.get(inbound_segment.flight_id)
            onward = flights.get(onward_segment.flight_id)
            if inbound is None or onward is None:
                continue

            if affected_flight_ids is not None and inbound.flight_id not in affected_flight_ids:
                continue

            earliest = inbound.revised_arrival + timedelta(minutes=minimum_connection_minutes)
            if earliest <= onward.scheduled_departure:
                continue

            shortfall = int((onward.scheduled_departure - earliest).total_seconds() // 60)
            broken.append(
                BrokenConnection(
                    booking_id=itinerary.booking_id,
                    pnr=itinerary.pnr,
                    passenger_id=itinerary.passenger_id,
                    passenger_reference=itinerary.passenger_reference,
                    tier=itinerary.tier,
                    has_special_needs=itinerary.has_special_needs,
                    inbound_segment_id=inbound_segment.segment_id,
                    inbound_flight_id=inbound.flight_id,
                    inbound_flight_number=inbound.flight_number,
                    inbound_scheduled_arrival=inbound.scheduled_arrival,
                    inbound_revised_arrival=inbound.revised_arrival,
                    inbound_delay_minutes=inbound.delay_minutes,
                    onward_segment_id=onward_segment.segment_id,
                    onward_flight_id=onward.flight_id,
                    onward_flight_number=onward.flight_number,
                    onward_scheduled_departure=onward.scheduled_departure,
                    connection_airport_icao=inbound.destination_icao,
                    minimum_connection_minutes=minimum_connection_minutes,
                    shortfall_minutes=shortfall,
                    recovered_by_onward_delay=earliest <= onward.revised_departure,
                )
            )
            # One booking, one broken itinerary. Counting every downstream segment would
            # inflate the total.
            break

    return ConnectionAssessment(
        at_risk=broken,
        connecting_itineraries_examined=connecting,
        single_segment_itineraries=single,
        minimum_connection_minutes=minimum_connection_minutes,
    )


class ConnectionService:
    name = "connection"

    async def execute(self, **kwargs: Any) -> ServiceResult:
        """Detect at-risk connections.

        Inputs:
            itineraries:            list[Itinerary]
            flights:                dict[int, SegmentFlight]
            affected_flight_ids:    optional set[int] to scope the walk to the incident
            business_constraints:   rows supplying the minimum connection time
        """
        itineraries = kwargs.get("itineraries")
        flights = kwargs.get("flights")

        if itineraries is None or flights is None:
            missing = [
                name
                for name, value in (("itineraries", itineraries), ("flights", flights))
                if value is None
            ]
            return ServiceResult(
                status=ActionStatus.needs_human,
                reason=(
                    "Connection assessment needs the itineraries and their flights. "
                    f"Missing: {', '.join(missing)}. Reporting zero broken connections "
                    "without them would read as good news."
                ),
                payload={"rule_version": RULE_VERSION},
                provenance_kind=ProvenanceKind.unavailable.value,
            )

        minimum = _minimum_connection_minutes(kwargs.get("business_constraints"))
        affected = kwargs.get("affected_flight_ids")

        assessment = find_at_risk_connections(
            itineraries=list(itineraries),
            flights=dict(flights),
            minimum_connection_minutes=minimum,
            affected_flight_ids=set(affected) if affected else None,
        )

        by_flight: dict[str, int] = {}
        for item in assessment.at_risk:
            by_flight[item.inbound_flight_number] = by_flight.get(item.inbound_flight_number, 0) + 1

        evidence: list[str] = []
        for item in assessment.at_risk:
            evidence.append(f"booking:{item.booking_id}")
            evidence.append(f"booking_segment:{item.inbound_segment_id}")
            evidence.append(f"booking_segment:{item.onward_segment_id}")
            evidence.append(f"flight:{item.inbound_flight_id}")
            evidence.append(f"flight:{item.onward_flight_id}")

        recovered = [item for item in assessment.at_risk if item.recovered_by_onward_delay]

        return ServiceResult(
            status=ActionStatus.success,
            reason=(
                f"{assessment.count} itineraries no longer feasible across "
                f"{len(by_flight)} flights, from "
                f"{assessment.connecting_itineraries_examined} connecting itineraries examined"
            ),
            payload={
                "rule_version": RULE_VERSION,
                "minimum_connection_minutes": minimum,
                "at_risk_count": assessment.count,
                "at_risk_by_flight": by_flight,
                "connecting_itineraries_examined": (assessment.connecting_itineraries_examined),
                "single_segment_itineraries": assessment.single_segment_itineraries,
                "recovered_by_onward_delay_count": len(recovered),
                "at_risk": [item.model_dump(mode="json") for item in assessment.at_risk],
            },
            evidence_refs=sorted(set(evidence)),
            provenance_kind=ProvenanceKind.synthetic.value,
        )
