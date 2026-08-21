"""Connection: the boundary condition is the minimum connection time, and the count must
be traceable rather than asserted.

The scenario target of 22 is checked against the seeded generator, and — more importantly —
so is the fact that not every connecting itinerary breaks. A count where everything breaks
would prove nothing about the logic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from data.generators.cascade_spec import BENGALURU_STORM
from data.generators.scenario_dataset import build_scenario_dataset

from app.models.enums import ActionStatus, ProvenanceKind
from app.services.connection import (
    DEFAULT_MINIMUM_CONNECTION_MINUTES,
    RULE_VERSION,
    ConnectionService,
    Itinerary,
    ItinerarySegment,
    SegmentFlight,
    find_at_risk_connections,
)

BASE = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


@pytest.fixture
def service() -> ConnectionService:
    return ConnectionService()


def _flight(flight_id: int, *, departs_after: int, block: int = 90, delay: int = 0):
    return SegmentFlight(
        flight_id=flight_id,
        flight_number=f"6E {flight_id}",
        origin_icao="VOBL",
        destination_icao="VIDP",
        scheduled_departure=BASE + timedelta(minutes=departs_after),
        scheduled_arrival=BASE + timedelta(minutes=departs_after + block),
        delay_minutes=delay,
    )


def _itinerary(booking_id: int, flight_ids: list[int]) -> Itinerary:
    return Itinerary(
        booking_id=booking_id,
        pnr=f"PNR{booking_id:03d}",
        passenger_id=booking_id,
        passenger_reference=f"PAX-{booking_id:05d}",
        segments=tuple(
            ItinerarySegment(segment_id=booking_id * 10 + order, segment_order=order, flight_id=fid)
            for order, fid in enumerate(flight_ids, start=1)
        ),
    )


# ----------------------------------------------------------------- the scenario target


@pytest.fixture(scope="module")
def scenario_inputs():
    """The seeded dataset, assembled exactly as `load_connection_inputs` assembles it."""
    dataset = build_scenario_dataset()

    flights = {
        flight.flight_id: SegmentFlight(
            flight_id=flight.flight_id,
            flight_number=flight.flight_number,
            origin_icao=flight.origin_icao,
            destination_icao=flight.destination_icao,
            scheduled_departure=flight.scheduled_departure,
            scheduled_arrival=flight.scheduled_arrival,
            delay_minutes=flight.delay_minutes,
        )
        for flight in [
            *BENGALURU_STORM.affected_flights,
            *BENGALURU_STORM.support_flights,
            *dataset.onward_flights,
        ]
    }

    segments_by_pnr: dict[str, list] = {}
    for segment in dataset.segments:
        segments_by_pnr.setdefault(segment.booking_pnr, []).append(segment)

    passengers = {row.reference: row for row in dataset.passengers}
    itineraries = [
        Itinerary(
            booking_id=index,
            pnr=booking.pnr,
            passenger_id=index,
            passenger_reference=booking.passenger_reference,
            tier=passengers[booking.passenger_reference].tier,
            has_special_needs=passengers[booking.passenger_reference].has_special_needs,
            segments=tuple(
                ItinerarySegment(
                    segment_id=segment.segment_id,
                    segment_order=segment.segment_order,
                    flight_id=segment.flight_id,
                )
                for segment in segments_by_pnr[booking.pnr]
            ),
        )
        for index, booking in enumerate(dataset.bookings, start=1)
    ]
    return itineraries, flights, dataset


def test_scenario_produces_exactly_twenty_two(scenario_inputs):
    itineraries, flights, _ = scenario_inputs
    result = find_at_risk_connections(
        itineraries=itineraries,
        flights=flights,
        affected_flight_ids={f.flight_id for f in BENGALURU_STORM.affected_flights},
    )
    assert result.count == 22


def test_not_every_connecting_itinerary_breaks(scenario_inputs):
    """The discriminating property. If all 38 broke, 22 would tell us nothing."""
    itineraries, flights, _ = scenario_inputs
    result = find_at_risk_connections(
        itineraries=itineraries,
        flights=flights,
        affected_flight_ids={f.flight_id for f in BENGALURU_STORM.affected_flights},
    )
    assert result.connecting_itineraries_examined == 38
    assert result.count < result.connecting_itineraries_examined


def test_two_delayed_flights_break_no_connections_at_all(scenario_inputs):
    """AI 611 and 6E 297 are delayed and have connecting passengers who still make it, so
    the assessment is per itinerary rather than per flight."""
    itineraries, flights, _ = scenario_inputs
    result = find_at_risk_connections(
        itineraries=itineraries,
        flights=flights,
        affected_flight_ids={f.flight_id for f in BENGALURU_STORM.affected_flights},
    )
    broken_flights = {item.inbound_flight_number for item in result.at_risk}
    assert "AI 611" not in broken_flights
    assert "6E 297" not in broken_flights


def test_per_flight_breakdown_matches_the_generator(scenario_inputs):
    itineraries, flights, dataset = scenario_inputs
    result = find_at_risk_connections(
        itineraries=itineraries,
        flights=flights,
        affected_flight_ids={f.flight_id for f in BENGALURU_STORM.affected_flights},
    )
    by_flight: dict[int, int] = {}
    for item in result.at_risk:
        by_flight[item.inbound_flight_id] = by_flight.get(item.inbound_flight_id, 0) + 1

    expected = {k: v for k, v in dataset.expected_at_risk_by_flight.items() if v}
    assert by_flight == expected


# ------------------------------------------------------------------ boundary condition


@pytest.mark.parametrize(
    ("delay", "expected_broken"),
    [
        # Scheduled turnaround is 60 minutes and the minimum is 45, so 15 minutes of delay
        # is absorbed exactly and 16 breaks it.
        (0, False),
        (14, False),
        (15, False),
        (16, True),
        (120, True),
    ],
)
def test_minimum_connection_time_is_the_boundary(delay: int, expected_broken: bool):
    inbound = _flight(1, departs_after=0, delay=delay)
    onward = _flight(2, departs_after=90 + 60)
    result = find_at_risk_connections(
        itineraries=[_itinerary(1, [1, 2])],
        flights={1: inbound, 2: onward},
        minimum_connection_minutes=45,
    )
    assert (result.count == 1) is expected_broken


def test_minimum_connection_time_comes_from_business_constraints():
    inbound = _flight(1, departs_after=0, delay=30)
    onward = _flight(2, departs_after=90 + 60)
    itineraries = [_itinerary(1, [1, 2])]
    flights = {1: inbound, 2: onward}

    lenient = find_at_risk_connections(
        itineraries=itineraries, flights=flights, minimum_connection_minutes=20
    )
    strict = find_at_risk_connections(
        itineraries=itineraries, flights=flights, minimum_connection_minutes=45
    )
    assert lenient.count == 0
    assert strict.count == 1


async def test_service_reads_the_constraint_row(service):
    inbound = _flight(1, departs_after=0, delay=30)
    onward = _flight(2, departs_after=150)
    result = await service.execute(
        itineraries=[_itinerary(1, [1, 2])],
        flights={1: inbound, 2: onward},
        business_constraints=[
            {
                "service": "connection_service",
                "constraint_key": "minimum_connection_minutes",
                "constraint_value": {"minutes": 20},
            }
        ],
    )
    assert result.payload["minimum_connection_minutes"] == 20
    assert result.payload["at_risk_count"] == 0


async def test_service_falls_back_to_the_seeded_default(service):
    result = await service.execute(itineraries=[], flights={})
    assert result.payload["minimum_connection_minutes"] == DEFAULT_MINIMUM_CONNECTION_MINUTES


# ------------------------------------------------------------------------- counting


def test_single_segment_itineraries_are_not_connections():
    result = find_at_risk_connections(
        itineraries=[_itinerary(1, [1])],
        flights={1: _flight(1, departs_after=0, delay=600)},
    )
    assert result.count == 0
    assert result.single_segment_itineraries == 1
    assert result.connecting_itineraries_examined == 0


def test_a_booking_is_counted_once_even_with_several_broken_legs():
    """A four-segment itinerary breaking at segment two is one broken itinerary, not three."""
    flights = {
        1: _flight(1, departs_after=0, delay=300),
        2: _flight(2, departs_after=150),
        3: _flight(3, departs_after=300),
        4: _flight(4, departs_after=450),
    }
    result = find_at_risk_connections(itineraries=[_itinerary(1, [1, 2, 3, 4])], flights=flights)
    assert result.count == 1
    assert result.at_risk[0].onward_segment_id == 12


def test_scoping_to_affected_flights_ignores_unrelated_delays():
    """Only the incident's flights are assessed; a delay elsewhere is another incident."""
    flights = {
        1: _flight(1, departs_after=0, delay=0),
        2: _flight(2, departs_after=150, delay=0),
        3: _flight(3, departs_after=300, delay=400),
        4: _flight(4, departs_after=450),
    }
    result = find_at_risk_connections(
        itineraries=[_itinerary(1, [1, 2]), _itinerary(2, [3, 4])],
        flights=flights,
        affected_flight_ids={1},
    )
    assert result.count == 0


def test_an_unknown_flight_id_is_skipped_not_guessed():
    result = find_at_risk_connections(
        itineraries=[_itinerary(1, [1, 99])],
        flights={1: _flight(1, departs_after=0, delay=600)},
    )
    assert result.count == 0


def test_ordering_of_input_does_not_change_the_result():
    flights = {
        1: _flight(1, departs_after=0, delay=300),
        2: _flight(2, departs_after=150),
        3: _flight(3, departs_after=0, delay=300),
        4: _flight(4, departs_after=150),
    }
    itineraries = [_itinerary(1, [1, 2]), _itinerary(2, [3, 4])]
    forward = find_at_risk_connections(itineraries=itineraries, flights=flights)
    backward = find_at_risk_connections(itineraries=list(reversed(itineraries)), flights=flights)
    assert [item.model_dump(mode="json") for item in forward.at_risk] == [
        item.model_dump(mode="json") for item in backward.at_risk
    ]


# --------------------------------------------------------------------- traceability


def test_every_result_names_its_booking_and_both_segments(scenario_inputs):
    """A count a controller cannot trace is a count they cannot defend."""
    itineraries, flights, _ = scenario_inputs
    result = find_at_risk_connections(
        itineraries=itineraries,
        flights=flights,
        affected_flight_ids={f.flight_id for f in BENGALURU_STORM.affected_flights},
    )
    for item in result.at_risk:
        assert item.booking_id > 0
        assert item.pnr
        assert item.passenger_reference.startswith("PAX-")
        assert item.inbound_segment_id != item.onward_segment_id
        assert item.shortfall_minutes < 0
        assert item.connection_airport_icao


async def test_evidence_refs_cover_every_broken_itinerary(service, scenario_inputs):
    itineraries, flights, _ = scenario_inputs
    result = await service.execute(
        itineraries=itineraries,
        flights=flights,
        affected_flight_ids={f.flight_id for f in BENGALURU_STORM.affected_flights},
    )
    refs = set(result.evidence_refs)
    for item in result.payload["at_risk"]:
        assert f"booking:{item['booking_id']}" in refs
        assert f"booking_segment:{item['inbound_segment_id']}" in refs
        assert f"booking_segment:{item['onward_segment_id']}" in refs


async def test_reason_is_specific(service, scenario_inputs):
    itineraries, flights, _ = scenario_inputs
    result = await service.execute(
        itineraries=itineraries,
        flights=flights,
        affected_flight_ids={f.flight_id for f in BENGALURU_STORM.affected_flights},
    )
    assert result.reason.startswith("22 itineraries no longer feasible")
    assert result.status is ActionStatus.success


# --------------------------------------------------- onward delay does not hide a break


def test_a_delayed_onward_flight_is_flagged_rather_than_dropped():
    """Judged against the scheduled departure, so the count cannot depend on the order the
    two delays were applied. The recovery hint is recorded separately."""
    inbound = _flight(1, departs_after=0, delay=120)
    onward = SegmentFlight(
        flight_id=2,
        flight_number="6E 2",
        origin_icao="VIDP",
        destination_icao="VOBL",
        scheduled_departure=BASE + timedelta(minutes=150),
        scheduled_arrival=BASE + timedelta(minutes=240),
        delay_minutes=180,
    )
    result = find_at_risk_connections(
        itineraries=[_itinerary(1, [1, 2])], flights={1: inbound, 2: onward}
    )
    assert result.count == 1
    assert result.at_risk[0].recovered_by_onward_delay is True


async def test_recovered_count_is_reported_separately(service):
    inbound = _flight(1, departs_after=0, delay=120)
    onward = SegmentFlight(
        flight_id=2,
        flight_number="6E 2",
        origin_icao="VIDP",
        destination_icao="VOBL",
        scheduled_departure=BASE + timedelta(minutes=150),
        scheduled_arrival=BASE + timedelta(minutes=240),
        delay_minutes=180,
    )
    result = await service.execute(
        itineraries=[_itinerary(1, [1, 2])], flights={1: inbound, 2: onward}
    )
    assert result.payload["recovered_by_onward_delay_count"] == 1


# ------------------------------------------------------------------- missing inputs


@pytest.mark.parametrize("missing", ["itineraries", "flights"])
async def test_missing_input_is_needs_human_not_zero(service, missing):
    """Zero broken connections because nothing was supplied would read as good news."""
    kwargs = {"itineraries": [], "flights": {}}
    kwargs.pop(missing)
    result = await service.execute(**kwargs)
    assert result.status is ActionStatus.needs_human
    assert missing in result.reason
    assert result.provenance_kind == ProvenanceKind.unavailable.value


async def test_empty_but_supplied_inputs_are_a_valid_zero(service):
    """Genuinely no connecting passengers is a real answer, distinct from missing data."""
    result = await service.execute(itineraries=[], flights={})
    assert result.status is ActionStatus.success
    assert result.payload["at_risk_count"] == 0
    assert result.payload["rule_version"] == RULE_VERSION


# ------------------------------------------------------------------- reproducibility


async def test_identical_input_yields_identical_output(service, scenario_inputs):
    itineraries, flights, _ = scenario_inputs
    first = await service.execute(itineraries=itineraries, flights=flights)
    second = await service.execute(itineraries=itineraries, flights=flights)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
