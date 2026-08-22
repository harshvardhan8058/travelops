"""Hotel search and allocation — STREAM C (C2-7).

Two separate action types, because they are two different decisions:

* `find_hotel_options` is a **read**. It ranks properties that satisfy the constraints. Nothing
  is committed and nothing becomes unavailable to anyone else.
* `reserve_hotel_block` is a **write**. It places holds against specific properties and, when
  there are not enough rooms, says so by name and number.

Splitting them matters for authorisation: a controller can see the options without anything
being taken off the market, and the act of taking rooms is a distinct, separately approvable
event with its own evidence.

**Availability is `total_rooms - sum(active holds)`, never a mutated counter.** `hotel` carries
an `available_rooms` column from Phase 1 and this service deliberately does not decrement it.
Two reasons. Concurrent allocations against a counter lose updates, and a demo that
double-books under a fast click is a demo that has to be explained. More importantly, a counter
cannot be replayed: after a reset there is no way to show *why* a property had six rooms left.
A hold ledger answers both — every room taken names the action that took it.

**Capacity is deliberately short.** Within the INR 6,000 cap the six eligible properties hold 71
rooms; 174 stranded passengers at two per room need 87. The gap is the point. A recovery tool
that always succeeds teaches an operator nothing, so this returns a partial allocation, a named
shortfall and `needs_human` — the honest outcome — rather than quietly spilling over the rate
cap or rounding the requirement down.

The escalation is `needs_human` rather than a failure, because the allocation that *was* made is
real and should stand. The remaining 16 rooms are a decision for a person: raise the cap, use a
property further out, or accept that some passengers wait.

Owner: Stream C.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ActionStatus, ProvenanceKind
from app.services.base import ServiceResult

RULE_VERSION = "hotel-v1"

BUSINESS_CONSTRAINT_SERVICE = "hotel_service"
KEY_MAX_RATE = "max_rate_inr"
KEY_PREFER_PARTNER = "prefer_partner"
KEY_PASSENGERS_PER_ROOM = "passengers_per_room"

DEFAULT_MAX_RATE_INR = 6000
DEFAULT_PASSENGERS_PER_ROOM = 2
DEFAULT_NIGHTS = 1


class HotelConstraints(BaseModel):
    """The rules the search runs under, read from `business_constraint`.

    Held as a value object with its own provenance list so a result can state which rows shaped
    it. A cap that came from a literal in this file could not be shown to anyone.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_rate_inr: int = DEFAULT_MAX_RATE_INR
    prefer_partner: bool = True
    passengers_per_room: int = DEFAULT_PASSENGERS_PER_ROOM
    nights: int = DEFAULT_NIGHTS
    #: Which constraint rows were actually found, so a default can be told from a real value.
    sourced_keys: tuple[str, ...] = ()

    @property
    def used_defaults(self) -> list[str]:
        return [
            key
            for key in (KEY_MAX_RATE, KEY_PREFER_PARTNER, KEY_PASSENGERS_PER_ROOM)
            if key not in self.sourced_keys
        ]


def load_constraints(rows: list[dict[str, Any]] | None) -> HotelConstraints:
    """Read caps and preferences from `business_constraint`, defaulting where absent."""
    values: dict[str, Any] = {}
    found: list[str] = []
    for row in rows or []:
        if row.get("service") != BUSINESS_CONSTRAINT_SERVICE:
            continue
        key = row.get("constraint_key")
        value = row.get("constraint_value")
        if not isinstance(value, dict):
            continue
        if key == KEY_MAX_RATE and "inr" in value:
            values["max_rate_inr"] = int(value["inr"])
            found.append(key)
        elif key == KEY_PREFER_PARTNER and "enabled" in value:
            values["prefer_partner"] = bool(value["enabled"])
            found.append(key)
        elif key == KEY_PASSENGERS_PER_ROOM and "count" in value:
            values["passengers_per_room"] = max(1, int(value["count"]))
            found.append(key)
    return HotelConstraints(**values, sourced_keys=tuple(found))


# ----------------------------------------------------------------------------- inputs


class HotelOption(BaseModel):
    """A property with its *computed* availability, not its stored counter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    hotel_id: int
    name: str
    airport_icao: str
    rate_inr: int
    is_partner: bool
    distance_km: float
    total_rooms: int
    #: `total_rooms` minus rooms already held. Derived; never read from `hotel.available_rooms`.
    rooms_held: int = 0

    @property
    def available_rooms(self) -> int:
        return max(0, self.total_rooms - self.rooms_held)


def rooms_required(*, passengers: int, constraints: HotelConstraints) -> int:
    """Passengers converted to rooms, always rounding **up**.

    174 passengers at two per room is 87 rooms, not 87.0 truncated to 87 by luck. Rounding down
    would leave someone in the terminal to satisfy an arithmetic convenience.
    """
    if passengers <= 0:
        return 0
    return math.ceil(passengers / max(1, constraints.passengers_per_room))


def rank_options(options: list[HotelOption], *, constraints: HotelConstraints) -> list[HotelOption]:
    """Eligible properties, best first.

    Eligibility is the rate cap and having a room. Order is partner preference (when the soft
    constraint is on), then distance, then rate, then id. Distance outranks rate because a
    coach ride at 02:00 costs more in goodwill than the difference between two room rates, and
    the id tiebreak means the ordering is total — two runs cannot disagree.
    """
    eligible = [
        option
        for option in options
        if option.rate_inr <= constraints.max_rate_inr and option.available_rooms > 0
    ]
    return sorted(
        eligible,
        key=lambda option: (
            not option.is_partner if constraints.prefer_partner else False,
            option.distance_km,
            option.rate_inr,
            option.hotel_id,
        ),
    )


# ---------------------------------------------------------------------------- outputs


class RoomAllocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hotel_id: int
    hotel_name: str
    rooms: int
    rate_inr: int
    nights: int
    cost_inr: int
    is_partner: bool
    distance_km: float
    detail: str


class AllocationResult(BaseModel):
    """What was allocated, what was not, and what it cost."""

    model_config = ConfigDict(extra="forbid")

    rooms_required: int
    rooms_allocated: int
    allocations: list[RoomAllocation] = Field(default_factory=list)
    total_cost_inr: int = 0

    #: Properties inside the cap but with no room left, and properties excluded by the cap.
    exhausted_hotel_ids: list[int] = Field(default_factory=list)
    excluded_by_rate_cap: list[int] = Field(default_factory=list)

    constraints_note: str = ""
    basis: Literal["persisted_records"] = "persisted_records"

    @property
    def shortfall_rooms(self) -> int:
        return max(0, self.rooms_required - self.rooms_allocated)

    @property
    def is_complete(self) -> bool:
        return self.shortfall_rooms == 0

    @property
    def shortfall_note(self) -> str:
        """The sentence an operator has to act on. Names the gap in rooms *and* in people."""
        if self.is_complete:
            return (
                f"All {self.rooms_required} rooms secured across "
                f"{len(self.allocations)} properties."
            )
        return (
            f"{self.rooms_allocated} of {self.rooms_required} rooms secured. "
            f"{self.shortfall_rooms} rooms short. Every property within the rate cap is "
            "exhausted, so closing the gap needs a decision: raise the cap, go further out, "
            "or accept that some passengers wait."
        )


def allocate_rooms(
    *,
    passengers: int,
    options: list[HotelOption],
    constraints: HotelConstraints,
) -> AllocationResult:
    """Fill the requirement from the ranked properties, best first. Pure and deterministic.

    Greedy by rank rather than optimising for cost. An optimiser would produce an allocation no
    one in the room could verify at 02:00, and the ranking already encodes the preference an
    operator would apply by hand.
    """
    required = rooms_required(passengers=passengers, constraints=constraints)
    ranked = rank_options(options, constraints=constraints)

    allocations: list[RoomAllocation] = []
    remaining = required
    for option in ranked:
        if remaining <= 0:
            break
        take = min(remaining, option.available_rooms)
        if take <= 0:
            continue
        cost = take * option.rate_inr * constraints.nights
        allocations.append(
            RoomAllocation(
                hotel_id=option.hotel_id,
                hotel_name=option.name,
                rooms=take,
                rate_inr=option.rate_inr,
                nights=constraints.nights,
                cost_inr=cost,
                is_partner=option.is_partner,
                distance_km=option.distance_km,
                detail=(
                    f"{take} of {option.available_rooms} available rooms at "
                    f"{option.name}, {option.distance_km} km out, "
                    f"INR {option.rate_inr} per night"
                    + (", partner property" if option.is_partner else "")
                ),
            )
        )
        remaining -= take

    within_cap = [o for o in options if o.rate_inr <= constraints.max_rate_inr]
    allocated_ids = {a.hotel_id for a in allocations}
    return AllocationResult(
        rooms_required=required,
        rooms_allocated=sum(a.rooms for a in allocations),
        allocations=allocations,
        total_cost_inr=sum(a.cost_inr for a in allocations),
        exhausted_hotel_ids=sorted(
            o.hotel_id
            for o in within_cap
            if o.hotel_id not in allocated_ids or o.available_rooms == 0
        ),
        excluded_by_rate_cap=sorted(
            o.hotel_id for o in options if o.rate_inr > constraints.max_rate_inr
        ),
        constraints_note=(
            f"Rate cap INR {constraints.max_rate_inr}, "
            f"{constraints.passengers_per_room} passengers per room, "
            f"partner preference {'on' if constraints.prefer_partner else 'off'}."
            + (
                f" Defaults used for: {', '.join(constraints.used_defaults)}."
                if constraints.used_defaults
                else ""
            )
        ),
    )


# ------------------------------------------------------------------------ availability


async def load_hotel_options(session: AsyncSession, *, airport_icao: str) -> list[HotelOption]:
    """Properties at an airport with availability computed from the hold ledger.

    The single reason this is async: availability is a query over `hotel_inventory_hold`, not a
    column read. `hotel.available_rooms` is intentionally ignored.
    """
    from app.models.cascade import HotelInventoryHold
    from app.models.reference import Hotel

    held_rows = (
        await session.execute(
            select(
                HotelInventoryHold.hotel_id,
                func.coalesce(func.sum(HotelInventoryHold.rooms), 0),
            )
            .where(HotelInventoryHold.released_at.is_(None))
            .group_by(HotelInventoryHold.hotel_id)
        )
    ).all()
    held = {int(hotel_id): int(rooms) for hotel_id, rooms in held_rows}

    hotels = (
        (
            await session.execute(
                select(Hotel).where(Hotel.airport_icao == airport_icao).order_by(Hotel.id)
            )
        )
        .scalars()
        .all()
    )
    return [
        HotelOption(
            hotel_id=hotel.id,
            name=hotel.name,
            airport_icao=hotel.airport_icao,
            rate_inr=int(hotel.rate_inr),
            is_partner=bool(hotel.is_partner),
            distance_km=float(hotel.distance_km),
            total_rooms=int(hotel.total_rooms),
            rooms_held=held.get(hotel.id, 0),
        )
        for hotel in hotels
    ]


async def place_holds(
    session: AsyncSession,
    *,
    result: AllocationResult,
    action_id: int | None,
    incident_group_id: int | None,
) -> int:
    """Record one hold per allocated property. Returns rooms held.

    Append-only. A release is a `released_at` timestamp on the existing row, never a delete, so
    the history of who took which rooms survives the room being given back.
    """
    from app.models.cascade import HotelInventoryHold

    for allocation in result.allocations:
        session.add(
            HotelInventoryHold(
                action_id=action_id,
                hotel_id=allocation.hotel_id,
                incident_group_id=incident_group_id,
                rooms=allocation.rooms,
                is_simulated=True,
            )
        )
    await session.flush()
    return result.rooms_allocated


# --------------------------------------------------------------------------- services


def _options_from(raw: Any) -> list[HotelOption]:
    return [item if isinstance(item, HotelOption) else HotelOption(**item) for item in (raw or [])]


class HotelSearchService:
    """`find_hotel_options`. A read: ranks properties, commits nothing."""

    name = "hotel_search"

    async def execute(self, **kwargs: Any) -> ServiceResult:
        options = kwargs.get("hotel_options")
        passengers = kwargs.get("passengers")

        if options is None or passengers is None:
            return ServiceResult(
                status=ActionStatus.needs_human,
                reason=(
                    "Hotel search needs the candidate properties and the passenger count. "
                    "An empty option list would read as 'no hotels available', which is a "
                    "very different statement from 'we did not look'."
                ),
                payload={"rule_version": RULE_VERSION},
                provenance_kind=ProvenanceKind.unavailable.value,
            )

        constraints = load_constraints(kwargs.get("business_constraints"))
        parsed = _options_from(options)
        ranked = rank_options(parsed, constraints=constraints)
        required = rooms_required(passengers=int(passengers), constraints=constraints)
        capacity = sum(option.available_rooms for option in ranked)

        return ServiceResult(
            status=ActionStatus.success,
            reason=(
                f"{len(ranked)} properties within the rate cap hold {capacity} rooms against "
                f"{required} required for {passengers} passengers"
            ),
            payload={
                "rule_version": RULE_VERSION,
                "rooms_required": required,
                "passengers": int(passengers),
                "eligible_capacity_rooms": capacity,
                "capacity_is_sufficient": capacity >= required,
                "constraints": constraints.model_dump(mode="json"),
                "options": [
                    {
                        **option.model_dump(mode="json"),
                        "available_rooms": option.available_rooms,
                        "rank": index,
                    }
                    for index, option in enumerate(ranked, start=1)
                ],
                "excluded_by_rate_cap": sorted(
                    option.hotel_id
                    for option in parsed
                    if option.rate_inr > constraints.max_rate_inr
                ),
                "scope_note": (
                    "A search, not a booking. Nothing is held and no property becomes "
                    "unavailable as a result of this action. Availability is computed from "
                    "the hold ledger, not read from a stored counter."
                ),
            },
            evidence_refs=sorted({f"hotel:{option.hotel_id}" for option in ranked}),
            provenance_kind=ProvenanceKind.synthetic.value,
        )


class HotelAllocationService:
    """`reserve_hotel_block`. A write: places holds and names any shortfall."""

    name = "hotel_allocation"

    async def execute(self, **kwargs: Any) -> ServiceResult:
        options = kwargs.get("hotel_options")
        passengers = kwargs.get("passengers")

        if options is None or passengers is None:
            return ServiceResult(
                status=ActionStatus.needs_human,
                reason=("Hotel allocation needs the candidate properties and the passenger count."),
                payload={"rule_version": RULE_VERSION},
                provenance_kind=ProvenanceKind.unavailable.value,
            )

        constraints = load_constraints(kwargs.get("business_constraints"))
        result = allocate_rooms(
            passengers=int(passengers),
            options=_options_from(options),
            constraints=constraints,
        )

        payload = {
            "rule_version": RULE_VERSION,
            "passengers": int(passengers),
            "rooms_required": result.rooms_required,
            "rooms_allocated": result.rooms_allocated,
            "shortfall_rooms": result.shortfall_rooms,
            "passengers_unaccommodated": result.shortfall_rooms * constraints.passengers_per_room,
            "total_cost_inr": result.total_cost_inr,
            "allocations": [a.model_dump(mode="json") for a in result.allocations],
            "excluded_by_rate_cap": result.excluded_by_rate_cap,
            "constraints_note": result.constraints_note,
            "shortfall_note": result.shortfall_note,
            "is_complete": result.is_complete,
            "basis": result.basis,
        }
        evidence = sorted({f"hotel:{a.hotel_id}" for a in result.allocations})

        if not result.is_complete:
            # The rooms secured are real and stand. The gap is a decision for a person, so it
            # escalates rather than failing — a failure would discard a good partial result.
            return ServiceResult(
                status=ActionStatus.needs_human,
                reason=result.shortfall_note,
                payload=payload,
                evidence_refs=evidence,
                provenance_kind=ProvenanceKind.synthetic.value,
            )

        return ServiceResult(
            status=ActionStatus.success,
            reason=(
                f"{result.rooms_allocated} rooms held across {len(result.allocations)} "
                f"properties for INR {result.total_cost_inr}"
            ),
            payload=payload,
            evidence_refs=evidence,
            provenance_kind=ProvenanceKind.synthetic.value,
        )


#: Phase 1 name, kept so nothing that imported it breaks. Search is the safe default: the read
#: cannot take rooms off the market by accident.
HotelService = HotelSearchService
