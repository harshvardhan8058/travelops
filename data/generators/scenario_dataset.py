"""The seeded scenario dataset: passengers, bookings, onward segments, hotels, constraints.

Scope is deliberately the **scenario**, not a whole operating day. The Stage 2 recovery path
needs the eight affected flights, the 604 passengers on them, the itineraries that break, the
nine rotations and the Bengaluru hotel set. The wide background network from
`docs/12-synthetic-data-plan.md` — ~400 flights, ~12,000 passengers, ~150 historical
incidents — is not required to demonstrate a recovery and is not generated here.

Determinism: `random.Random(SEED)` drawn in a fixed order, so the same seed produces the same
rows byte for byte. Nothing here reads a clock.

Working backwards from the targets, as `data/generators/README.md` requires:

* **604 passengers** — the per-flight counts in `cascade_spec` sum to exactly that.
* **22 at-risk connections** — generated as `at_risk_connections` breaking itineraries per
  flight, *plus* deliberately surviving ones on every flight. If every connecting itinerary
  broke, the number 22 would prove nothing about the logic; two flights have surviving
  connections and no broken ones at all.
* **11 candidate hotels** at VOBL, with capacity deliberately short of what the primary
  flight needs once the budget cap is applied, so partial allocation is exercised rather
  than assumed.

Owner: Stream C.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.services.connection import (
    BUSINESS_CONSTRAINT_KEY as CONNECTION_CONSTRAINT_KEY,
)
from app.services.connection import (
    BUSINESS_CONSTRAINT_SERVICE as CONNECTION_CONSTRAINT_SERVICE,
)
from app.services.connection import (
    DEFAULT_MINIMUM_CONNECTION_MINUTES,
)
from app.services.crew_impact import ScheduledFlight
from app.services.delay_risk import (
    BUSINESS_CONSTRAINT_KEY as DELAY_RISK_CONSTRAINT_KEY,
)
from app.services.delay_risk import (
    BUSINESS_CONSTRAINT_SERVICE as DELAY_RISK_CONSTRAINT_SERVICE,
)
from app.services.delay_risk import (
    DEFAULT_RULESET,
)
from data.generators import SEED
from data.generators.cascade_spec import BENGALURU_STORM, CascadeScenario

#: Surviving connections generated per affected flight. Present so the at-risk count is a
#: discriminating result rather than "every connecting passenger".
SAFE_CONNECTIONS_PER_FLIGHT = 2

#: Onward flight ids start here, well clear of the cascade's own ids.
ONWARD_FLIGHT_ID_BASE = 200

#: Tight onward: scheduled 60 minutes after the inbound's scheduled arrival. Feasible as
#: published (60 >= the 45-minute minimum) and broken by any delay over 15 minutes.
TIGHT_CONNECTION_MINUTES = 60
#: Loose onward: three hours after the *revised* arrival, so it survives the disruption.
LOOSE_CONNECTION_BUFFER_MINUTES = 180

ONWARD_BLOCK_MINUTES = 90

TIER_WEIGHTS: tuple[tuple[str, int], ...] = (
    ("standard", 80),
    ("silver", 13),
    ("gold", 5),
    ("platinum", 2),
)
SPECIAL_NEEDS_RATE = 0.03

#: Cabin by tier. Values must fit `booking.cabin` (String(12)).
_CABIN_BY_TIER: dict[str, str] = {
    "standard": "economy",
    "silver": "economy",
    "gold": "premium",
    "platinum": "business",
}

#: Visibly synthetic on inspection, and assembled from fixed syllables rather than a faker
#: dependency so no name can accidentally resemble a real person.
_GIVEN = (
    "Aarav",
    "Diya",
    "Vihaan",
    "Ananya",
    "Kabir",
    "Ishani",
    "Rohan",
    "Meera",
    "Arjun",
    "Saanvi",
    "Dev",
    "Kavya",
    "Nikhil",
    "Riya",
    "Aditya",
    "Tara",
)
_FAMILY = (
    "Sharma",
    "Iyer",
    "Nair",
    "Reddy",
    "Bose",
    "Menon",
    "Kulkarni",
    "Chandra",
    "Pillai",
    "Rao",
    "Verma",
    "Sethi",
    "Banerjee",
    "Kaur",
    "Joshi",
    "Mehta",
)
_PNR_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

EMAIL_DOMAIN = "example.com"

HOTEL_AIRPORT_ICAO = "VOBL"
#: The cap the Hotel service reads. Several hotels sit above it on purpose so the constraint
#: visibly bites.
HOTEL_MAX_RATE_INR = 6000
HOTEL_PASSENGERS_PER_ROOM = 2


@dataclass(frozen=True, slots=True)
class PassengerRow:
    reference: str
    full_name: str
    email: str
    phone: str
    tier: str
    has_special_needs: bool


@dataclass(frozen=True, slots=True)
class SegmentRow:
    segment_id: int
    booking_pnr: str
    flight_id: int
    segment_order: int


@dataclass(frozen=True, slots=True)
class BookingRow:
    pnr: str
    passenger_reference: str
    cabin: str
    one_way_basic_fare_inr: int
    airline_fuel_charge_inr: int
    payment_method: str


@dataclass(frozen=True, slots=True)
class HotelRow:
    name: str
    airport_icao: str
    rate_inr: int
    is_partner: bool
    distance_km: float
    total_rooms: int
    available_rooms: int


@dataclass(frozen=True, slots=True)
class ConstraintRow:
    service: str
    constraint_key: str
    constraint_value: dict
    is_hard: bool
    version: str
    description: str


@dataclass(frozen=True, slots=True)
class ScenarioDataset:
    passengers: list[PassengerRow]
    bookings: list[BookingRow]
    segments: list[SegmentRow]
    onward_flights: list[ScheduledFlight]
    hotels: list[HotelRow]
    constraints: list[ConstraintRow]
    #: flight_id -> number of itineraries generated to break on it. Sums to 22.
    expected_at_risk_by_flight: dict[int, int] = field(default_factory=dict)

    @property
    def expected_at_risk_total(self) -> int:
        return sum(self.expected_at_risk_by_flight.values())


def _pnr(rng: random.Random) -> str:
    return "".join(rng.choice(_PNR_ALPHABET) for _ in range(6))


def _onward_flight(
    *,
    flight_id: int,
    flight_number: str,
    origin_icao: str,
    destination_icao: str,
    scheduled_departure: datetime,
) -> ScheduledFlight:
    return ScheduledFlight(
        flight_id=flight_id,
        flight_number=flight_number,
        origin_icao=origin_icao,
        destination_icao=destination_icao,
        scheduled_departure=scheduled_departure,
        scheduled_arrival=scheduled_departure + timedelta(minutes=ONWARD_BLOCK_MINUTES),
        delay_minutes=0,
        passengers=0,
    )


def _onward_destination(origin_icao: str, offset: int) -> str:
    """Pick an onward destination that is not where the passenger already is.

    Deterministic by position in the airport list, not random, so the itinerary graph is
    stable across runs and readable in the fixture.
    """
    candidates = [
        "VOBL",
        "VIDP",
        "VABB",
        "VOHS",
        "VOMM",
        "VECC",
        "VOCI",
        "VOGO",
        "VAAH",
        "VAPO",
    ]
    options = [icao for icao in candidates if icao != origin_icao]
    return options[offset % len(options)]


def build_scenario_dataset(
    scenario: CascadeScenario = BENGALURU_STORM, *, seed: int = SEED
) -> ScenarioDataset:
    """Generate the scenario dataset deterministically."""
    rng = random.Random(seed)

    onward_flights: list[ScheduledFlight] = []
    tight_by_flight: dict[int, int] = {}
    loose_by_flight: dict[int, int] = {}

    for index, spec in enumerate(scenario.affected):
        inbound = spec.flight
        tight_id = ONWARD_FLIGHT_ID_BASE + index * 2
        loose_id = ONWARD_FLIGHT_ID_BASE + index * 2 + 1

        tight = _onward_flight(
            flight_id=tight_id,
            flight_number=f"6E 9{tight_id}",
            origin_icao=inbound.destination_icao,
            destination_icao=_onward_destination(inbound.destination_icao, index),
            scheduled_departure=(
                inbound.scheduled_arrival + timedelta(minutes=TIGHT_CONNECTION_MINUTES)
            ),
        )
        loose = _onward_flight(
            flight_id=loose_id,
            flight_number=f"6E 9{loose_id}",
            origin_icao=inbound.destination_icao,
            destination_icao=_onward_destination(inbound.destination_icao, index + 1),
            scheduled_departure=(
                inbound.revised_arrival + timedelta(minutes=LOOSE_CONNECTION_BUFFER_MINUTES)
            ),
        )
        onward_flights.extend([tight, loose])
        tight_by_flight[inbound.flight_id] = tight_id
        loose_by_flight[inbound.flight_id] = loose_id

    passengers: list[PassengerRow] = []
    bookings: list[BookingRow] = []
    segments: list[SegmentRow] = []
    used_pnrs: set[str] = set()

    passenger_number = 0
    segment_id = 0
    expected_at_risk: dict[int, int] = {}

    for spec in scenario.affected:
        inbound = spec.flight
        at_risk_target = spec.at_risk_connections
        expected_at_risk[inbound.flight_id] = at_risk_target

        for seat_index in range(inbound.passengers):
            passenger_number += 1
            reference = f"PAX-{passenger_number:05d}"

            given = _GIVEN[rng.randrange(len(_GIVEN))]
            family = _FAMILY[rng.randrange(len(_FAMILY))]
            tier = rng.choices(
                [name for name, _ in TIER_WEIGHTS],
                weights=[weight for _, weight in TIER_WEIGHTS],
                k=1,
            )[0]
            special_needs = rng.random() < SPECIAL_NEEDS_RATE

            passengers.append(
                PassengerRow(
                    reference=reference,
                    full_name=f"{given} {family}",
                    # Non-routable by construction. There is no code path that stores real
                    # personal data.
                    email=f"{reference.lower()}@{EMAIL_DOMAIN}",
                    # Documented fictional range, and never dialled: SMS is simulated.
                    phone=f"+91 90000 {passenger_number:05d}",
                    tier=tier,
                    has_special_needs=special_needs,
                )
            )

            pnr = _pnr(rng)
            while pnr in used_pnrs:
                pnr = _pnr(rng)
            used_pnrs.add(pnr)

            bookings.append(
                BookingRow(
                    pnr=pnr,
                    passenger_reference=reference,
                    # Constrained to `booking.cabin`'s String(12); "premium_economy" does
                    # not fit and Postgres rejects it where SQLite would have accepted it.
                    cabin=_CABIN_BY_TIER[tier],
                    one_way_basic_fare_inr=rng.randrange(2800, 9200, 100),
                    airline_fuel_charge_inr=rng.randrange(400, 1200, 50),
                    payment_method=rng.choice(["card", "upi", "netbanking"]),
                )
            )

            segment_id += 1
            segments.append(
                SegmentRow(
                    segment_id=segment_id,
                    booking_pnr=pnr,
                    flight_id=inbound.flight_id,
                    segment_order=1,
                )
            )

            onward_id: int | None = None
            if seat_index < at_risk_target:
                onward_id = tight_by_flight[inbound.flight_id]
            elif seat_index < at_risk_target + SAFE_CONNECTIONS_PER_FLIGHT:
                onward_id = loose_by_flight[inbound.flight_id]

            if onward_id is not None:
                segment_id += 1
                segments.append(
                    SegmentRow(
                        segment_id=segment_id,
                        booking_pnr=pnr,
                        flight_id=onward_id,
                        segment_order=2,
                    )
                )

    hotels = _build_hotels(rng, scenario)
    constraints = _build_constraints()

    return ScenarioDataset(
        passengers=passengers,
        bookings=bookings,
        segments=segments,
        onward_flights=onward_flights,
        hotels=hotels,
        constraints=constraints,
        expected_at_risk_by_flight=expected_at_risk,
    )


def _build_hotels(rng: random.Random, scenario: CascadeScenario) -> list[HotelRow]:
    """Eleven hotels at VOBL, with a deliberate capacity shortfall.

    The rates straddle the configured cap and the room counts are chosen so that the stock
    reachable *within* the cap cannot accommodate the primary flight. A recovery where
    everything succeeds demonstrates nothing; a controlled shortfall exercises prioritisation
    and the partial-resolution path.
    """
    names = (
        "Airport Transit Inn",
        "Devanahalli Grand",
        "Kempegowda Suites",
        "Northgate Residency",
        "Trumpet Junction Hotel",
        "Hebbal Park Lodge",
        "Yelahanka Comfort",
        "Skyline Airport Hotel",
        "Bagalur Courtyard",
        "Terminal View Rooms",
        "Doddaballapur Retreat",
    )
    # Rates straddle the cap: six at or below, five above.
    rates = (2500, 3200, 3900, 4600, 5200, 5900, 6400, 7100, 7800, 8600, 9500)
    rooms = (18, 14, 12, 10, 9, 8, 40, 35, 30, 25, 20)

    hotels: list[HotelRow] = []
    for index, (name, rate, total) in enumerate(zip(names, rates, rooms, strict=True)):
        hotels.append(
            HotelRow(
                name=name,
                airport_icao=HOTEL_AIRPORT_ICAO,
                rate_inr=rate,
                # Deterministic rather than random so "partner hotels first" is reproducible.
                is_partner=index % 5 in {0, 2},
                distance_km=round(1.5 + index * 2.1, 2),
                total_rooms=total,
                available_rooms=total,
            )
        )

    primary = scenario.affected[0].flight
    rooms_needed = -(-primary.passengers // HOTEL_PASSENGERS_PER_ROOM)
    within_cap = sum(h.available_rooms for h in hotels if h.rate_inr <= HOTEL_MAX_RATE_INR)
    if within_cap >= rooms_needed:  # pragma: no cover - guarded by a test
        raise AssertionError(
            f"hotel capacity within the cap ({within_cap} rooms) is not short of the "
            f"{rooms_needed} rooms {primary.flight_number} needs; the shortfall the "
            f"scenario depends on has been generated away"
        )
    # `rng` is accepted for signature symmetry with the other builders and to keep the draw
    # order stable if this ever becomes randomised.
    del rng
    return hotels


def _build_constraints() -> list[ConstraintRow]:
    """Thresholds as data, so no service holds a literal.

    The delay-risk ruleset is stored whole and hashed, which is what lets a recorded
    prediction be replayed against the exact numbers that produced it.
    """
    return [
        ConstraintRow(
            service=DELAY_RISK_CONSTRAINT_SERVICE,
            constraint_key=DELAY_RISK_CONSTRAINT_KEY,
            constraint_value=DEFAULT_RULESET.model_dump(mode="json"),
            is_hard=True,
            version=DEFAULT_RULESET.version,
            description=(
                "Banded delay-risk ruleset. An ordered index, not a calibrated probability."
            ),
        ),
        ConstraintRow(
            service=CONNECTION_CONSTRAINT_SERVICE,
            constraint_key=CONNECTION_CONSTRAINT_KEY,
            constraint_value={"minutes": DEFAULT_MINIMUM_CONNECTION_MINUTES},
            is_hard=True,
            version="v1",
            description="Minimum domestic connection time used to test itinerary feasibility.",
        ),
        ConstraintRow(
            service="hotel_service",
            constraint_key="max_rate_inr",
            constraint_value={"inr": HOTEL_MAX_RATE_INR},
            is_hard=True,
            version="v1",
            description="Nightly rate cap. Read from here, never as a literal in the service.",
        ),
        ConstraintRow(
            service="hotel_service",
            constraint_key="prefer_partner",
            constraint_value={"enabled": True},
            is_hard=False,
            version="v1",
            description="Soft preference for partner properties when rate and capacity allow.",
        ),
        ConstraintRow(
            service="hotel_service",
            constraint_key="passengers_per_room",
            constraint_value={"count": HOTEL_PASSENGERS_PER_ROOM},
            is_hard=False,
            version="v1",
            description="Occupancy assumption used to convert passengers into rooms required.",
        ),
    ]
