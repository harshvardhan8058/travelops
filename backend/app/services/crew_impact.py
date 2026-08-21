"""Crew Impact service — STREAM C.

Walk pairing legs FORWARD from the affected flights and report which pairings are at risk,
each with the mechanism that put it at risk:

    operating       crew are working the affected flight
    onward_duty     a later leg of the same pairing is now infeasible
    second_pairing  cockpit and cabin sit on different pairings
    positioning     crew were deadheading to operate another flight

The mechanism becomes the edge label in the cascade graph, which is what lets a reviewer
read why nine rotations are affected by eight flights instead of trusting a headline.

SCOPE BOUNDARY: coordination and display only. This service must NEVER validate duty-time
legality or generate a legal replacement roster. Concretely, it does not read
`crew_member.duty_hours_limit` at all — the safest way to honour that boundary is for the
legality-adjacent column to be untouched by this code. Feasibility here is turnaround
arithmetic against `pairing_leg.min_connection_minutes` and nothing more.

Two further boundaries, both deliberate:

1. **Feasibility is judged against the SCHEDULED departure of the onward leg**, matching
   the Connection service. The statement being made is "this rotation cannot be flown as
   published", which is the fact a controller needs. Whether it could be flown against a
   revised departure depends on the crew's duty envelope, which this system does not
   adjudicate.
2. **The forward walk does not expand to second-order flights.** It finds pairings holding
   a leg on a storm-affected flight and then walks forward *within* each such pairing.
   Letting a newly-at-risk flight pull in further pairings makes the cascade unbounded and
   the count unverifiable.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ActionStatus, PairingLegRole, PairingMechanism, ProvenanceKind
from app.services.base import ServiceResult

RULE_VERSION = "crew-impact-v1"

#: Times are rendered in the operating timezone because a controller reads local time.
DISPLAY_TZ = ZoneInfo("Asia/Kolkata")


# --------------------------------------------------------------------------- inputs


class ScheduledFlight(BaseModel):
    """A flight as the roster sees it, with whatever delay currently applies.

    Deliberately a plain value object rather than an ORM row so the attribution logic can
    be exercised without a database and reused by the dataset generator.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    flight_id: int
    flight_number: str
    origin_icao: str
    destination_icao: str
    scheduled_departure: datetime
    scheduled_arrival: datetime
    delay_minutes: int = 0
    passengers: int = 0

    @property
    def revised_departure(self) -> datetime:
        return self.scheduled_departure + timedelta(minutes=self.delay_minutes)

    @property
    def revised_arrival(self) -> datetime:
        return self.scheduled_arrival + timedelta(minutes=self.delay_minutes)

    @property
    def route(self) -> str:
        return f"{self.origin_icao} \u2192 {self.destination_icao}"


class RosterLeg(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    leg_id: int
    leg_order: int
    flight_id: int
    role: PairingLegRole
    #: Minimum turnaround before the next leg is infeasible. Drives forward propagation.
    min_connection_minutes: int = 45


class RosterPairing(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pairing_id: int
    reference: str
    base_icao: str
    legs: tuple[RosterLeg, ...]

    @property
    def ordered_legs(self) -> list[RosterLeg]:
        return sorted(self.legs, key=lambda leg: leg.leg_order)


# --------------------------------------------------------------------------- output


class PairingImpact(BaseModel):
    """One explainable reason a rotation is at risk.

    Maps onto the `pairing_impact` table and onto Stream D's `CrewPairingImpact`.
    """

    model_config = ConfigDict(extra="forbid")

    pairing_id: int
    pairing_reference: str
    base_icao: str

    #: The delayed flight that propagated to this pairing.
    source_flight_id: int
    source_flight_number: str

    #: The leg that is actually compromised — the delayed leg itself for `operating` and
    #: `second_pairing`, the downstream leg that breaks for `onward_duty` and `positioning`.
    affected_leg_id: int
    affected_leg_order: int
    affected_leg_flight_number: str
    pairing_leg_count: int

    mechanism: PairingMechanism
    detail: str
    is_at_risk: bool = True

    #: Every affected flight this pairing holds a leg on. One pairing spanning two affected
    #: flights is what makes eight flights resolve to seven carrying rotations.
    covered_flight_ids: list[int] = Field(default_factory=list)

    @property
    def affected_leg_label(self) -> str:
        return f"leg {self.affected_leg_order} of {self.pairing_leg_count}"


# --------------------------------------------------------------------- attribution


def _clock(moment: datetime, *, reference: datetime) -> str:
    """Local time, with an explicit day marker when it rolls past midnight.

    A bare "00:55" next to a 21:25 departure is the kind of ambiguity that makes a
    reviewer distrust the whole table.
    """
    local = moment.astimezone(DISPLAY_TZ)
    day_offset = (local.date() - reference.astimezone(DISPLAY_TZ).date()).days
    suffix = f" (+{day_offset})" if day_offset > 0 else ""
    return f"{local:%H:%M}{suffix} IST"


def _first_infeasible_downstream_leg(
    *,
    pairing: RosterPairing,
    entry_leg: RosterLeg,
    flights: dict[int, ScheduledFlight],
) -> RosterLeg | None:
    """Propagate the delay forward through the pairing and return the first leg that breaks.

    Absorption is the normal case: if the crew make the next departure with the minimum
    connection intact, the rotation runs to schedule from there and nothing downstream
    breaks. Reporting an absorbed delay as a break would inflate the count.
    """
    legs = pairing.ordered_legs
    ordered = [leg for leg in legs if leg.leg_order > entry_leg.leg_order]

    previous_leg = entry_leg
    available_from = flights[entry_leg.flight_id].revised_arrival

    for leg in ordered:
        flight = flights[leg.flight_id]
        earliest = available_from + timedelta(minutes=previous_leg.min_connection_minutes)
        if earliest > flight.scheduled_departure:
            return leg
        # Made the connection: this leg operates to schedule and absorbs the delay.
        available_from = flight.scheduled_arrival
        previous_leg = leg

    return None


def attribute_pairing_impacts(
    *,
    affected_flights: list[ScheduledFlight],
    pairings: list[RosterPairing],
    flights: dict[int, ScheduledFlight],
) -> list[PairingImpact]:
    """Attribute exactly one mechanism to every pairing touched by the affected flights.

    Precedence, first match wins:

        1. positioning     the pairing's entry leg is a positioning leg and a later
                           operating leg is now infeasible
        2. onward_duty     a later leg of the pairing is now infeasible
        3. second_pairing  another pairing is already recorded for the same flight
        4. operating       residual: the crew are working the affected flight

    `onward_duty` deliberately outranks `operating`. When both are true, naming the
    downstream leg that breaks carries strictly more information for a controller than
    restating that the crew are on a delayed aircraft.

    Determinism comes from fixed iteration order — pairings by reference, ascending — so
    the same roster always produces the same attribution regardless of how the caller
    ordered its rows.
    """
    affected_ids = {flight.flight_id for flight in affected_flights}

    #: Which flights already have a rotation recorded as carrying them. Drives rule 3.
    carried: dict[int, str] = {}
    impacts: list[PairingImpact] = []

    for pairing in sorted(pairings, key=lambda p: p.reference):
        legs_on_affected = [leg for leg in pairing.ordered_legs if leg.flight_id in affected_ids]
        if not legs_on_affected:
            continue

        entry_leg = legs_on_affected[0]
        source = flights[entry_leg.flight_id]
        covered = sorted({leg.flight_id for leg in legs_on_affected})
        leg_count = len(pairing.legs)
        broken = _first_infeasible_downstream_leg(
            pairing=pairing, entry_leg=entry_leg, flights=flights
        )

        if entry_leg.role is PairingLegRole.positioning:
            if broken is None:
                # Crew were riding as passengers and nothing downstream broke. Recorded so
                # the row exists and is inspectable, but not counted as at risk.
                impacts.append(
                    _build(
                        pairing=pairing,
                        source=source,
                        affected_leg=entry_leg,
                        leg_count=leg_count,
                        mechanism=PairingMechanism.positioning,
                        covered=covered,
                        detail=(
                            f"Crew were positioning on {source.flight_number}. The delay is "
                            f"absorbed before their next operating leg, so this rotation is "
                            f"not at risk."
                        ),
                        is_at_risk=False,
                        flights=flights,
                    )
                )
                continue

            onward = flights[broken.flight_id]
            detail = (
                f"Crew were positioning on {source.flight_number} to operate "
                f"{onward.flight_number}, scheduled "
                f"{_clock(onward.scheduled_departure, reference=source.scheduled_departure)}. "
                f"{source.flight_number} now arrives "
                f"{_clock(source.revised_arrival, reference=source.scheduled_departure)}, so "
                f"{onward.flight_number} loses its crew although it was never delayed."
            )
            impacts.append(
                _build(
                    pairing=pairing,
                    source=source,
                    affected_leg=broken,
                    leg_count=leg_count,
                    mechanism=PairingMechanism.positioning,
                    covered=covered,
                    detail=detail,
                    flights=flights,
                )
            )
            continue

        if broken is not None:
            onward = flights[broken.flight_id]
            detail = (
                f"{source.flight_number} arrives "
                f"{_clock(source.revised_arrival, reference=source.scheduled_departure)}, "
                f"{source.delay_minutes} minutes late. The crew's next duty "
                f"{onward.flight_number} is scheduled "
                f"{_clock(onward.scheduled_departure, reference=source.scheduled_departure)} "
                f"and needs {entry_leg.min_connection_minutes} minutes minimum connection, so "
                f"it cannot be operated as published."
            )
            carried.setdefault(entry_leg.flight_id, pairing.reference)
            impacts.append(
                _build(
                    pairing=pairing,
                    source=source,
                    affected_leg=broken,
                    leg_count=leg_count,
                    mechanism=PairingMechanism.onward_duty,
                    covered=covered,
                    detail=detail,
                    flights=flights,
                )
            )
            continue

        if entry_leg.flight_id in carried:
            detail = (
                f"A second rotation on {source.flight_number}: {carried[entry_leg.flight_id]} "
                f"already carries this flight, so this is a distinct crew complement — "
                f"cockpit and cabin sit on different pairings."
            )
            impacts.append(
                _build(
                    pairing=pairing,
                    source=source,
                    affected_leg=entry_leg,
                    leg_count=leg_count,
                    mechanism=PairingMechanism.second_pairing,
                    covered=covered,
                    detail=detail,
                    flights=flights,
                )
            )
            continue

        carried.setdefault(entry_leg.flight_id, pairing.reference)
        detail = (
            f"Crew are operating {source.flight_number}, delayed {source.delay_minutes} "
            f"minutes to "
            f"{_clock(source.revised_departure, reference=source.scheduled_departure)}. No "
            f"later leg of this pairing becomes infeasible, so the rotation is disrupted at "
            f"this leg only."
        )
        impacts.append(
            _build(
                pairing=pairing,
                source=source,
                affected_leg=entry_leg,
                leg_count=leg_count,
                mechanism=PairingMechanism.operating,
                covered=covered,
                detail=detail,
                flights=flights,
            )
        )

    return impacts


def _build(
    *,
    pairing: RosterPairing,
    source: ScheduledFlight,
    affected_leg: RosterLeg,
    leg_count: int,
    mechanism: PairingMechanism,
    covered: list[int],
    detail: str,
    flights: dict[int, ScheduledFlight],
    is_at_risk: bool = True,
) -> PairingImpact:
    return PairingImpact(
        pairing_id=pairing.pairing_id,
        pairing_reference=pairing.reference,
        base_icao=pairing.base_icao,
        source_flight_id=source.flight_id,
        source_flight_number=source.flight_number,
        affected_leg_id=affected_leg.leg_id,
        affected_leg_order=affected_leg.leg_order,
        affected_leg_flight_number=flights[affected_leg.flight_id].flight_number,
        pairing_leg_count=leg_count,
        mechanism=mechanism,
        detail=detail,
        is_at_risk=is_at_risk,
        covered_flight_ids=covered,
    )


def explain_identity(impacts: list[PairingImpact]) -> str:
    """The structural sentence behind the count, computed from the records.

    Never a claim: every number in the string is derived from `impacts`.
    """
    at_risk = [impact for impact in impacts if impact.is_at_risk]
    carriers = [
        impact
        for impact in at_risk
        if impact.mechanism in {PairingMechanism.operating, PairingMechanism.onward_duty}
    ]
    extras = [
        impact
        for impact in at_risk
        if impact.mechanism in {PairingMechanism.second_pairing, PairingMechanism.positioning}
    ]
    covered: set[int] = set()
    for impact in carriers:
        covered.update(impact.covered_flight_ids)
    spanning = [impact for impact in carriers if len(impact.covered_flight_ids) > 1]

    parts = [
        f"{len(covered)} affected flights are carried by {len(carriers)} rotations",
    ]
    for impact in spanning:
        parts.append(
            f"{impact.pairing_reference} spans "
            f"{len(impact.covered_flight_ids)} of them, which is why the rotation count is "
            f"lower than the flight count"
        )
    for impact in extras:
        parts.append(f"{impact.pairing_reference} is additional via {impact.mechanism.value}")
    parts.append(f"{len(carriers)} + {len(extras)} = {len(at_risk)} rotations at risk")
    return ". ".join(parts) + "."


# --------------------------------------------------------------------------- service


class CrewImpactService:
    name = "crew_impact"

    async def execute(self, **kwargs: object) -> ServiceResult:
        """Report affected pairings with the mechanism for each.

        Inputs (supplied by the orchestrator, which has already consulted the Decision
        Assurance Gate):

            affected_flights: list[ScheduledFlight]
            pairings:         list[RosterPairing]
            flights:          dict[int, ScheduledFlight]   all flights the roster touches
        """
        affected_flights = kwargs.get("affected_flights")
        pairings = kwargs.get("pairings")
        flights = kwargs.get("flights")

        if not affected_flights or pairings is None or flights is None:
            return ServiceResult(
                status=ActionStatus.needs_human,
                reason=(
                    "Crew impact needs the affected flights and the roster. Missing: "
                    + ", ".join(
                        name
                        for name, value in (
                            ("affected_flights", affected_flights),
                            ("pairings", pairings),
                            ("flights", flights),
                        )
                        if not value and value != []
                    )
                ),
                payload={"rule_version": RULE_VERSION},
                provenance_kind=ProvenanceKind.unavailable.value,
            )

        assert isinstance(affected_flights, list)
        assert isinstance(pairings, list)
        assert isinstance(flights, dict)

        impacts = attribute_pairing_impacts(
            affected_flights=affected_flights, pairings=pairings, flights=flights
        )
        at_risk = [impact for impact in impacts if impact.is_at_risk]

        mechanism_counts: dict[str, int] = {}
        for impact in at_risk:
            mechanism_counts[impact.mechanism.value] = (
                mechanism_counts.get(impact.mechanism.value, 0) + 1
            )

        evidence_refs = [f"flight:{flight.flight_id}" for flight in affected_flights]
        evidence_refs += [f"pairing:{impact.pairing_id}" for impact in at_risk]
        evidence_refs += [f"pairing_leg:{impact.affected_leg_id}" for impact in at_risk]

        return ServiceResult(
            status=ActionStatus.success,
            reason=(
                f"{len(at_risk)} crew rotations at risk across "
                f"{len(affected_flights)} affected flights"
            ),
            payload={
                "rule_version": RULE_VERSION,
                "pairings_at_risk": len(at_risk),
                "mechanism_counts": mechanism_counts,
                "identity": explain_identity(impacts),
                "impacts": [impact.model_dump(mode="json") for impact in at_risk],
                "scope_note": (
                    "Coordination and display only. Duty-time legality is not validated "
                    "and no replacement roster is generated."
                ),
            },
            evidence_refs=sorted(set(evidence_refs)),
            provenance_kind=ProvenanceKind.synthetic.value,
        )
