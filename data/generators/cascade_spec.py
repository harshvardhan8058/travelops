"""The deliberate Bengaluru storm cascade — the single source of truth for the nine.

This module is the answer to the mentor review question "why 9 crew rotations and not 8?".
It is hand-constructed on purpose. Randomness cannot be trusted to produce a roster whose
pairing count is both exactly nine and defensible leg by leg, so the roster is built
backwards from the target and every property is asserted in
`backend/tests/unit/services/test_crew_cascade_counts.py`.

The structural identity, which the tests prove rather than assume:

    8 affected flights
      -> 7 rotations carry them          PAIR-E1 spans two of them: it arrives at BLR on
                                        the delayed UK 705 and is rostered to operate
                                        UK 812, so two flights share one rotation
      +  1 second rotation               PAIR-A2, the cabin complement on 6E 2134, sits on
                                        a different pairing from the cockpit crew
      +  1 positioning rotation          PAIR-B2 deadheads home on 6E 811 to operate a
                                        Mumbai departure that was never delayed
      =  9

There is no delay threshold anywhere in this file. A threshold would have to be reverse
engineered from the answer — the fixture needs AI 503 at 65 minutes counted and UK 705 at
70 minutes not counted on its own account, which no cutoff can do — and a reviewer who
found it would be right to distrust everything beside it.

One flight differs from the earlier Wave 0 fixture: **UK 705 is an arrival, AMD -> BLR.**
A storm at Bengaluru delays inbounds at least as much as departures, and inbound crew
feeding an outbound is the canonical cascade. Without it, all eight flights would be BLR
departures inside one 2.5-hour window, no rotation could span two of them, and the count
would be forced to 8 + 1 — leaving two of the four mechanisms permanently off the graph.

Provenance: schedules here are SYNTHETIC. They are published-style, not a real timetable,
and must be labelled `synthetic` until the AIKosh file is archived with its licence and a
loader contract test passes.

Owner: Stream C.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

from app.models.enums import PairingLegRole
from app.services.crew_impact import RosterLeg, RosterPairing, ScheduledFlight

IST = ZoneInfo("Asia/Kolkata")

SCENARIO_KEY = "bengaluru_storm"
DEMO_DATASET_ID = "bengaluru_storm"

#: The airport the storm sits over.
ROOT_AIRPORT_ICAO = "VOBL"

#: Storm injection, per data/fixtures/bengaluru_storm.yaml: 2026-08-20T15:36:00Z.
INJECTED_AT = datetime(2026, 8, 20, 21, 6, tzinfo=IST)

#: The ten-airport set. IATA is display only; ICAO is the key everywhere in the schema.
IATA_BY_ICAO = {
    "VOBL": "BLR",
    "VIDP": "DEL",
    "VABB": "BOM",
    "VOHS": "HYD",
    "VOMM": "MAA",
    "VECC": "CCU",
    "VOCI": "COK",
    "VOGO": "GOI",
    "VAAH": "AMD",
    "VAPO": "PNQ",
}

#: Default crew turnaround. Held on every leg so the propagation rule reads one value from
#: the row rather than a constant in the service.
MIN_CONNECTION_MINUTES = 45


def _ist(day: int, hour: int, minute: int) -> datetime:
    """A local time on 2026-08-20 or the day after. Stored as UTC downstream."""
    return datetime(2026, 8, day, hour, minute, tzinfo=IST)


class AffectedFlightSpec(BaseModel):
    """An affected flight plus the fixture-facing facts that hang off it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    flight: ScheduledFlight
    #: Workflow state for the incident on this flight, as the group detail renders it.
    state: str
    #: Passengers on this flight whose onward segment breaks. Sums to the target of 22.
    at_risk_connections: int
    #: The flight the disruption group is named for. Declared, because "primary" is an
    #: editorial fact about the scenario and cannot be recovered from the schedule. Exactly
    #: one affected flight sets it, and a partial unique index enforces that in the database.
    is_primary: bool = False

    @property
    def membership_role(self) -> str:
        """`incident_group_flight.role` for this flight.

        Arrival versus departure IS a fact about the flight — the group's airport is its
        destination rather than its origin — so it is derived. Membership itself is not:
        this method is only ever called for flights already listed in `_AFFECTED`.
        """
        if self.is_primary:
            return "primary"
        return (
            "affected_arrival"
            if self.flight.destination_icao == ROOT_AIRPORT_ICAO
            else "affected_departure"
        )


class CascadeScenario(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_key: str
    demo_dataset_id: str
    root_airport_icao: str
    injected_at: datetime
    affected: tuple[AffectedFlightSpec, ...]
    support_flights: tuple[ScheduledFlight, ...]
    pairings: tuple[RosterPairing, ...]
    crew_assignments: tuple[tuple[str, str], ...]
    #: Hotels generated within search range of the root airport.
    candidate_hotel_target: int
    #: Capacity is deliberately short so partial allocation is exercised, not assumed.
    hotel_capacity_shortfall: bool

    @property
    def affected_flights(self) -> list[ScheduledFlight]:
        return [spec.flight for spec in self.affected]

    @property
    def flights_by_id(self) -> dict[int, ScheduledFlight]:
        return {
            flight.flight_id: flight for flight in [*self.affected_flights, *self.support_flights]
        }

    @property
    def membership(self) -> list[tuple[int, str, int]]:
        """`(flight_id, role, delay_minutes)` for every member flight, ordered by flight id.

        This is the whole declaration of which flights the group covers. Nothing derives
        membership from `origin_icao == airport_icao`: that query returns seven of the eight,
        because UK 705 arrives into VOBL rather than departing it. Seven flights still yield
        nine pairings, so the count looks right while the `onward_duty` mechanism silently
        vanishes — a wrong answer wearing the right number.
        """
        return sorted(
            (spec.flight.flight_id, spec.membership_role, spec.flight.delay_minutes)
            for spec in self.affected
        )

    @property
    def at_risk_connections_by_flight(self) -> dict[int, int]:
        return {spec.flight.flight_id: spec.at_risk_connections for spec in self.affected}

    @property
    def passengers_affected(self) -> int:
        return sum(flight.passengers for flight in self.affected_flights)


# --------------------------------------------------------------------------------------
# Affected flights. Every departure is at or after 20:50 and UK 705's revised departure is
# 21:15, so all eight sit inside the storm window rather than being pre-existing delays.
# Passenger counts sum to exactly 604.
# --------------------------------------------------------------------------------------

_AFFECTED: tuple[AffectedFlightSpec, ...] = (
    AffectedFlightSpec(
        flight=ScheduledFlight(
            flight_id=1,
            flight_number="6E 2134",
            origin_icao="VOBL",
            destination_icao="VIDP",
            scheduled_departure=_ist(20, 21, 10),
            scheduled_arrival=_ist(20, 23, 55),
            delay_minutes=420,
            passengers=174,
        ),
        state="executing",
        at_risk_connections=8,
        is_primary=True,
    ),
    AffectedFlightSpec(
        flight=ScheduledFlight(
            flight_id=2,
            flight_number="6E 811",
            origin_icao="VOBL",
            destination_icao="VABB",
            scheduled_departure=_ist(20, 21, 25),
            scheduled_arrival=_ist(20, 23, 5),
            delay_minutes=110,
            passengers=158,
        ),
        state="assuring",
        at_risk_connections=5,
    ),
    AffectedFlightSpec(
        flight=ScheduledFlight(
            flight_id=3,
            flight_number="AI 503",
            origin_icao="VOBL",
            destination_icao="VOHS",
            scheduled_departure=_ist(20, 20, 50),
            scheduled_arrival=_ist(20, 22, 5),
            delay_minutes=65,
            passengers=96,
        ),
        state="planning",
        at_risk_connections=3,
    ),
    AffectedFlightSpec(
        flight=ScheduledFlight(
            flight_id=5,
            flight_number="6E 455",
            origin_icao="VOBL",
            destination_icao="VOMM",
            scheduled_departure=_ist(20, 21, 35),
            scheduled_arrival=_ist(20, 22, 35),
            delay_minutes=95,
            passengers=72,
        ),
        state="planning",
        at_risk_connections=2,
    ),
    AffectedFlightSpec(
        flight=ScheduledFlight(
            flight_id=6,
            flight_number="UK 812",
            origin_icao="VOBL",
            destination_icao="VECC",
            scheduled_departure=_ist(20, 22, 40),
            scheduled_arrival=_ist(21, 1, 20),
            delay_minutes=140,
            passengers=41,
        ),
        state="detected",
        at_risk_connections=2,
    ),
    AffectedFlightSpec(
        flight=ScheduledFlight(
            flight_id=7,
            flight_number="AI 611",
            origin_icao="VOBL",
            destination_icao="VOCI",
            scheduled_departure=_ist(20, 21, 50),
            scheduled_arrival=_ist(20, 23, 5),
            delay_minutes=55,
            passengers=33,
        ),
        state="detected",
        at_risk_connections=0,
    ),
    AffectedFlightSpec(
        flight=ScheduledFlight(
            flight_id=8,
            flight_number="6E 297",
            origin_icao="VOBL",
            destination_icao="VOGO",
            scheduled_departure=_ist(20, 21, 15),
            scheduled_arrival=_ist(20, 22, 25),
            delay_minutes=80,
            passengers=18,
        ),
        state="detected",
        at_risk_connections=0,
    ),
    # The inbound. This single change is what lets one rotation span two affected flights.
    AffectedFlightSpec(
        flight=ScheduledFlight(
            flight_id=9,
            flight_number="UK 705",
            origin_icao="VAAH",
            destination_icao="VOBL",
            scheduled_departure=_ist(20, 20, 5),
            scheduled_arrival=_ist(20, 21, 55),
            delay_minutes=70,
            passengers=12,
        ),
        state="detected",
        at_risk_connections=2,
    ),
)


# --------------------------------------------------------------------------------------
# Support flights: the rest of each pairing. None of these is storm-affected, which is the
# point — several of them break anyway, and that is the cascade.
# --------------------------------------------------------------------------------------

_SUPPORT: tuple[ScheduledFlight, ...] = (
    ScheduledFlight(
        flight_id=101,
        flight_number="6E 2135",
        origin_icao="VIDP",
        destination_icao="VOBL",
        scheduled_departure=_ist(21, 14, 0),
        scheduled_arrival=_ist(21, 16, 45),
        passengers=168,
    ),
    ScheduledFlight(
        flight_id=102,
        flight_number="6E 2160",
        origin_icao="VOBL",
        destination_icao="VOHS",
        scheduled_departure=_ist(21, 18, 0),
        scheduled_arrival=_ist(21, 19, 15),
        passengers=152,
    ),
    ScheduledFlight(
        flight_id=103,
        flight_number="6E 2161",
        origin_icao="VOHS",
        destination_icao="VOBL",
        scheduled_departure=_ist(21, 20, 0),
        scheduled_arrival=_ist(21, 21, 15),
        passengers=147,
    ),
    ScheduledFlight(
        flight_id=104,
        flight_number="6E 2181",
        origin_icao="VIDP",
        destination_icao="VAPO",
        scheduled_departure=_ist(21, 15, 10),
        scheduled_arrival=_ist(21, 17, 15),
        passengers=161,
    ),
    ScheduledFlight(
        flight_id=105,
        flight_number="6E 2182",
        origin_icao="VAPO",
        destination_icao="VOBL",
        scheduled_departure=_ist(21, 18, 5),
        scheduled_arrival=_ist(21, 19, 30),
        passengers=139,
    ),
    ScheduledFlight(
        flight_id=106,
        flight_number="6E 812",
        origin_icao="VABB",
        destination_icao="VOBL",
        scheduled_departure=_ist(21, 6, 30),
        scheduled_arrival=_ist(21, 8, 10),
        passengers=171,
    ),
    ScheduledFlight(
        flight_id=107,
        flight_number="6E 830",
        origin_icao="VABB",
        destination_icao="VOBL",
        scheduled_departure=_ist(20, 16, 10),
        scheduled_arrival=_ist(20, 17, 50),
        passengers=166,
    ),
    # The flight PAIR-B2 was rostered to operate. Never delayed, loses its crew anyway.
    ScheduledFlight(
        flight_id=108,
        flight_number="6E 289",
        origin_icao="VABB",
        destination_icao="VOGO",
        scheduled_departure=_ist(20, 23, 50),
        scheduled_arrival=_ist(21, 0, 50),
        passengers=118,
    ),
    ScheduledFlight(
        flight_id=109,
        flight_number="6E 290",
        origin_icao="VOGO",
        destination_icao="VABB",
        scheduled_departure=_ist(21, 7, 0),
        scheduled_arrival=_ist(21, 8, 0),
        passengers=124,
    ),
    ScheduledFlight(
        flight_id=110,
        flight_number="AI 504",
        origin_icao="VOHS",
        destination_icao="VOBL",
        scheduled_departure=_ist(21, 7, 0),
        scheduled_arrival=_ist(21, 8, 20),
        passengers=88,
    ),
    ScheduledFlight(
        flight_id=111,
        flight_number="6E 401",
        origin_icao="VOBL",
        destination_icao="VOCI",
        scheduled_departure=_ist(20, 15, 10),
        scheduled_arrival=_ist(20, 16, 25),
        passengers=143,
    ),
    ScheduledFlight(
        flight_id=112,
        flight_number="6E 402",
        origin_icao="VOCI",
        destination_icao="VOBL",
        scheduled_departure=_ist(20, 17, 15),
        scheduled_arrival=_ist(20, 18, 30),
        passengers=137,
    ),
    ScheduledFlight(
        flight_id=113,
        flight_number="6E 456",
        origin_icao="VOMM",
        destination_icao="VOHS",
        scheduled_departure=_ist(21, 7, 5),
        scheduled_arrival=_ist(21, 8, 20),
        passengers=131,
    ),
    ScheduledFlight(
        flight_id=114,
        flight_number="6E 457",
        origin_icao="VOHS",
        destination_icao="VOBL",
        scheduled_departure=_ist(21, 9, 10),
        scheduled_arrival=_ist(21, 10, 25),
        passengers=155,
    ),
    ScheduledFlight(
        flight_id=115,
        flight_number="UK 704",
        origin_icao="VOBL",
        destination_icao="VAAH",
        scheduled_departure=_ist(20, 17, 30),
        scheduled_arrival=_ist(20, 19, 20),
        passengers=94,
    ),
    ScheduledFlight(
        flight_id=116,
        flight_number="UK 813",
        origin_icao="VECC",
        destination_icao="VOBL",
        scheduled_departure=_ist(21, 8, 0),
        scheduled_arrival=_ist(21, 10, 40),
        passengers=102,
    ),
    ScheduledFlight(
        flight_id=117,
        flight_number="AI 612",
        origin_icao="VOCI",
        destination_icao="VOBL",
        scheduled_departure=_ist(21, 7, 30),
        scheduled_arrival=_ist(21, 8, 45),
        passengers=79,
    ),
    ScheduledFlight(
        flight_id=118,
        flight_number="6E 298",
        origin_icao="VOGO",
        destination_icao="VOBL",
        scheduled_departure=_ist(21, 8, 0),
        scheduled_arrival=_ist(21, 9, 0),
        passengers=112,
    ),
)


def _legs(*specs: tuple[int, int, PairingLegRole]) -> tuple[RosterLeg, ...]:
    """(leg_id, flight_id, role) in flown order."""
    return tuple(
        RosterLeg(
            leg_id=leg_id,
            leg_order=order,
            flight_id=flight_id,
            role=role,
            min_connection_minutes=MIN_CONNECTION_MINUTES,
        )
        for order, (leg_id, flight_id, role) in enumerate(specs, start=1)
    )


_OP = PairingLegRole.operating
_POS = PairingLegRole.positioning

# --------------------------------------------------------------------------------------
# The nine rotations. Each starts and ends at its base, is geographically continuous, and
# is feasible as published — the disruption is what breaks it. All three are asserted.
# --------------------------------------------------------------------------------------

_PAIRINGS: tuple[RosterPairing, ...] = (
    # Cockpit crew on the primary flight. Their next duty is next afternoon, so the
    # 420-minute delay is absorbed downstream: the rotation is disrupted at this leg only.
    RosterPairing(
        pairing_id=1,
        reference="PAIR-A1",
        base_icao="VOBL",
        legs=_legs((1, 1, _OP), (2, 101, _OP), (3, 102, _OP), (4, 103, _OP)),
    ),
    # Cabin crew on the same flight, on a different rotation. This is the +1 that the
    # `second_pairing` mechanism exists to name.
    RosterPairing(
        pairing_id=2,
        reference="PAIR-A2",
        base_icao="VOBL",
        legs=_legs((5, 1, _OP), (6, 104, _OP), (7, 105, _OP)),
    ),
    RosterPairing(
        pairing_id=3,
        reference="PAIR-B1",
        base_icao="VOBL",
        legs=_legs((8, 2, _OP), (9, 106, _OP)),
    ),
    # Mumbai-based crew: operate down to BLR, deadhead home on the delayed 6E 811, and are
    # rostered to operate 6E 289 to Goa at 23:50. 6E 289 was never delayed and had no crew
    # aboard 6E 811, and it still loses its crew. The hardest mechanism to argue with.
    RosterPairing(
        pairing_id=4,
        reference="PAIR-B2",
        base_icao="VABB",
        legs=_legs((10, 107, _OP), (11, 2, _POS), (12, 108, _OP), (13, 109, _OP)),
    ),
    RosterPairing(
        pairing_id=5,
        reference="PAIR-C1",
        base_icao="VOBL",
        legs=_legs((14, 3, _OP), (15, 110, _OP)),
    ),
    RosterPairing(
        pairing_id=6,
        reference="PAIR-D1",
        base_icao="VOBL",
        legs=_legs((16, 111, _OP), (17, 112, _OP), (18, 5, _OP), (19, 113, _OP), (20, 114, _OP)),
    ),
    # The rotation that spans two affected flights: it arrives at BLR on the delayed
    # UK 705 and is rostered to operate UK 812 45 minutes later. This is the -1 that turns
    # eight primary rotations into seven.
    RosterPairing(
        pairing_id=7,
        reference="PAIR-E1",
        base_icao="VOBL",
        legs=_legs((21, 115, _OP), (22, 9, _OP), (23, 6, _OP), (24, 116, _OP)),
    ),
    RosterPairing(
        pairing_id=8,
        reference="PAIR-F1",
        base_icao="VOBL",
        legs=_legs((25, 7, _OP), (26, 117, _OP)),
    ),
    RosterPairing(
        pairing_id=9,
        reference="PAIR-G1",
        base_icao="VOBL",
        legs=_legs((27, 8, _OP), (28, 118, _OP)),
    ),
)


# --------------------------------------------------------------------------------------
# Crew. Synthetic and visibly so. Cockpit and cabin share a rotation except on 6E 2134,
# which is the case the `second_pairing` mechanism reports.
# --------------------------------------------------------------------------------------

_CREW_ASSIGNMENTS: tuple[tuple[str, str], ...] = (
    ("CRW-0001", "PAIR-A1"),
    ("CRW-0002", "PAIR-A1"),
    ("CRW-0003", "PAIR-A2"),
    ("CRW-0004", "PAIR-A2"),
    ("CRW-0005", "PAIR-A2"),
    ("CRW-0006", "PAIR-A2"),
    ("CRW-0007", "PAIR-B1"),
    ("CRW-0008", "PAIR-B1"),
    ("CRW-0009", "PAIR-B1"),
    ("CRW-0010", "PAIR-B2"),
    ("CRW-0011", "PAIR-B2"),
    ("CRW-0012", "PAIR-C1"),
    ("CRW-0013", "PAIR-C1"),
    ("CRW-0014", "PAIR-C1"),
    ("CRW-0015", "PAIR-D1"),
    ("CRW-0016", "PAIR-D1"),
    ("CRW-0017", "PAIR-D1"),
    ("CRW-0018", "PAIR-E1"),
    ("CRW-0019", "PAIR-E1"),
    ("CRW-0020", "PAIR-E1"),
    ("CRW-0021", "PAIR-F1"),
    ("CRW-0022", "PAIR-F1"),
    ("CRW-0023", "PAIR-G1"),
    ("CRW-0024", "PAIR-G1"),
)


BENGALURU_STORM = CascadeScenario(
    scenario_key=SCENARIO_KEY,
    demo_dataset_id=DEMO_DATASET_ID,
    root_airport_icao=ROOT_AIRPORT_ICAO,
    injected_at=INJECTED_AT,
    affected=_AFFECTED,
    support_flights=_SUPPORT,
    pairings=_PAIRINGS,
    crew_assignments=_CREW_ASSIGNMENTS,
    candidate_hotel_target=11,
    hotel_capacity_shortfall=True,
)
