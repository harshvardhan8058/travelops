"""What-if — STREAM C (C2-9).

**Bounded, zero-write, deterministic re-evaluation. Explicitly not a simulation engine and not a
digital twin** — P2-D2.

What that means concretely. A what-if takes the facts already persisted for a group, substitutes
one or more declared inputs, re-runs the *same* deterministic functions the real services use,
and reports the difference. It does not model aircraft, crew legality, passenger behaviour,
weather evolution or knock-on scheduling. It cannot discover an effect the real services would
not have found, because it is the real services running over altered inputs.

Two properties are enforced rather than intended:

1. **Zero writes.** No `session.add`, no `flush`, no `commit` — the session is only ever read
   from. A test counts every row in the database before and after and asserts they are identical.
   A what-if that left a trace would corrupt the evidence trail it exists to explore, and the
   figure a controller trusted would depend on whether someone had clicked "what if" first.
2. **A closed set of levers.** Only the inputs in `ALLOWED_LEVERS` may be varied. An open
   parameter bag would let a caller reach into the ruleset, get a favourable answer, and present
   it with the same authority as a real assessment. If a lever is not declared here, the answer
   is a refusal that names the levers that do exist.

The honest framing for the screen: this says "if the delay were 60 minutes rather than 420, the
same rules would have found 9 broken connections rather than 22". It does not say what will
happen.

Owner: Stream C.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.scenario_queries import (
    CascadeRollup,
    cascade_rollup,
    group_affected_flights,
)

RULE_VERSION = "what-if-v1"

#: The only inputs a what-if may vary, each mapped to what it re-evaluates.
#:
#: Closed on purpose. Every entry is a fact an operator could genuinely change in the real world
#: — a delay improves, a connection minimum is waived, occupancy is doubled up, the rate cap is
#: raised. None of them lets the caller alter a *rule* and present the result as an assessment.
ALLOWED_LEVERS: dict[str, str] = {
    "delay_minutes_by_flight": "Re-evaluates connections and crew feasibility for those flights.",
    "minimum_connection_minutes": "Re-evaluates which connections hold.",
    "passengers_per_room": "Re-evaluates the room requirement.",
    "max_rate_inr": "Re-evaluates which properties are eligible and the resulting shortfall.",
    "max_expansion_depth": "Re-evaluates how far the crew cascade is followed.",
}


class LeverRejection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lever: str
    reason: str


class ScenarioDelta(BaseModel):
    """One figure, before and after, with the arithmetic left visible."""

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    baseline: int
    scenario: int

    @property
    def delta(self) -> int:
        return self.scenario - self.baseline

    @property
    def summary(self) -> str:
        if self.delta == 0:
            return f"{self.label}: unchanged at {self.baseline}."
        direction = "fewer" if self.delta < 0 else "more"
        return f"{self.label}: {self.baseline} -> {self.scenario} ({abs(self.delta)} {direction})."


class WhatIfResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_reference: str
    rule_version: str = RULE_VERSION
    levers_applied: dict[str, Any] = Field(default_factory=dict)
    levers_rejected: list[LeverRejection] = Field(default_factory=list)
    deltas: list[ScenarioDelta] = Field(default_factory=list)

    #: What the services actually recorded, alongside the re-evaluated baseline. Both are shown
    #: because they answer different questions — "what did we find" and "what do the rules say
    #: now" — and quietly presenting one as the other is how a what-if starts to look like a
    #: correction to the live figures.
    recorded_baseline: dict[str, int] = Field(default_factory=dict)

    #: Type-level statement of what this is. Cannot become "simulated" without an edit that
    #: forces someone to re-read P2-D2.
    basis: Literal["recorded_evidence"] = "recorded_evidence"
    wrote_rows: Literal[False] = False

    #: Restated in the payload because it is the boundary most likely to be over-read on a
    #: screen, by exactly the audience most likely to over-read it.
    boundary_note: str = (
        "A bounded re-evaluation of recorded facts under substituted inputs, using the same "
        "deterministic rules as the live services. Not a simulation, not a digital twin, and "
        "not a forecast: nothing is modelled that the services would not themselves have "
        "found, and no rows are written."
    )

    @property
    def is_no_op(self) -> bool:
        return all(delta.delta == 0 for delta in self.deltas)

    @property
    def headline(self) -> str:
        if not self.levers_applied:
            return "No recognised lever was supplied, so nothing was re-evaluated."
        if self.is_no_op:
            return "Every figure is unchanged under these inputs."
        return " ".join(delta.summary for delta in self.deltas if delta.delta != 0)


def validate_levers(levers: dict[str, Any]) -> tuple[dict[str, Any], list[LeverRejection]]:
    """Split a request into recognised levers and named refusals.

    A rejection names the lever and lists what is allowed, so a caller learns the boundary from
    the refusal rather than from a silently ignored parameter.
    """
    accepted: dict[str, Any] = {}
    rejected: list[LeverRejection] = []
    for name, value in (levers or {}).items():
        if name in ALLOWED_LEVERS:
            accepted[name] = value
            continue
        rejected.append(
            LeverRejection(
                lever=name,
                reason=(
                    f"'{name}' is not a what-if lever. This is a bounded re-evaluation, not a "
                    "simulation engine, so only recorded inputs may be substituted: "
                    + ", ".join(sorted(ALLOWED_LEVERS))
                    + "."
                ),
            )
        )
    return accepted, rejected


async def evaluate_what_if(
    session: AsyncSession,
    *,
    group_id: int,
    levers: dict[str, Any],
) -> WhatIfResult:
    """Re-evaluate the group's figures under substituted inputs. Reads only.

    Every query is a read. The baseline comes from the same `cascade_rollup` the live view uses,
    so a what-if cannot disagree with the screen it was launched from.
    """
    accepted, rejected = validate_levers(levers)
    baseline = await cascade_rollup(session, group_id=group_id)

    result = WhatIfResult(
        group_reference=baseline.group_reference,
        levers_applied=accepted,
        levers_rejected=rejected,
    )
    if not accepted:
        return result

    member_flight_ids = {
        member.flight_id for member in await group_affected_flights(session, group_id=group_id)
    }

    # Baseline and scenario both go through the SAME re-evaluation, the baseline with no
    # substitutions. That is what makes a delta attributable to the lever rather than to the
    # method: comparing a re-evaluation against the recorded rollup would fold in every
    # difference between "what the services found" and "what the rules say now", and a
    # controller would read that combined difference as the effect of their change.
    figures_before = await _re_evaluate(
        session, baseline=baseline, member_flight_ids=member_flight_ids, levers={}
    )
    figures_after = await _re_evaluate(
        session, baseline=baseline, member_flight_ids=member_flight_ids, levers=accepted
    )

    result.recorded_baseline = {
        "connections_at_risk": baseline.connections_at_risk,
        "crew_pairings_affected": baseline.crew_pairings_affected,
    }
    result.deltas = [
        ScenarioDelta(
            key=key,
            label=label,
            baseline=figures_before[key],
            scenario=figures_after[key],
        )
        for key, label in (
            ("connections_at_risk", "Connections that break"),
            ("crew_pairings_affected", "Crew rotations at risk"),
            ("rooms_required", "Rooms required"),
            ("rooms_short", "Rooms short"),
        )
    ]
    return result


async def _re_evaluate(
    session: AsyncSession,
    *,
    baseline: CascadeRollup,
    member_flight_ids: set[int],
    levers: dict[str, Any],
) -> dict[str, int]:
    """Run the real deterministic functions over substituted inputs. Reads only.

    Imports are local so this module stays a leaf: nothing here is on the live service path, and
    nothing on the live service path imports it. A what-if cannot become load-bearing by
    accident.
    """
    from app.db.scenario_queries import (
        load_business_constraints,
        load_connection_inputs,
        load_crew_impact_inputs,
    )
    from app.services.connection import (
        _minimum_connection_minutes,
        find_at_risk_connections,
    )
    from app.services.crew_impact import expand_crew_cascade
    from app.services.hotel import allocate_rooms, load_constraints, load_hotel_options

    constraint_rows = await load_business_constraints(session)
    overrides = {
        int(flight_id): int(minutes)
        for flight_id, minutes in (levers.get("delay_minutes_by_flight") or {}).items()
    }

    def substitute(flights: dict[int, Any]) -> dict[int, Any]:
        """Apply the delay lever. `model_copy` because the inputs are frozen value objects —
        which is also why a what-if physically cannot mutate the loaded rows."""
        return {
            flight_id: (
                flight.model_copy(update={"delay_minutes": overrides[flight_id]})
                if flight_id in overrides
                else flight
            )
            for flight_id, flight in flights.items()
        }

    # ------------------------------------------------------------------- connections
    connections_at_risk = 0
    if member_flight_ids:
        itineraries, flights = await load_connection_inputs(session, member_flight_ids)
        assessment = find_at_risk_connections(
            itineraries=itineraries,
            flights=substitute(flights),
            minimum_connection_minutes=int(
                levers.get("minimum_connection_minutes")
                or _minimum_connection_minutes(constraint_rows)
            ),
            affected_flight_ids=set(member_flight_ids),
        )
        # The union of bookings, exactly as cascade_rollup counts it.
        connections_at_risk = len({item.booking_id for item in assessment.at_risk})

    # -------------------------------------------------------------------------- crew
    crew_pairings_affected = 0
    if member_flight_ids:
        affected, pairings, crew_flights = await load_crew_impact_inputs(session, member_flight_ids)
        adjusted = substitute(crew_flights)
        cascade = expand_crew_cascade(
            affected_flights=[adjusted.get(f.flight_id, f) for f in affected],
            pairings=pairings,
            flights=adjusted,
            max_expansion_depth=max(1, int(levers.get("max_expansion_depth") or 1)),
        )
        # Deduplicated by reference, the same way the rollup does it.
        crew_pairings_affected = len(
            {item.pairing_reference for item in cascade.direct_at_risk}
            | {item.pairing_reference for item in cascade.downstream_at_risk}
        )

    # ------------------------------------------------------------------------ hotels
    constraints = load_constraints(constraint_rows).model_copy(
        update={
            key: int(levers[key])
            for key in ("passengers_per_room", "max_rate_inr")
            if key in levers
        }
    )
    # The room basis is the passengers who actually need a bed — those with no onward departure
    # left today — read from the recorded allocations. Using `passengers_affected` here would give
    # the what-if a different definition of "rooms required" from the live allocation, and two
    # definitions of one figure in one product is how a demo ends up contradicting itself on
    # screen.
    # The group's own holds are excluded from the inventory this re-evaluation sees.
    #
    # `load_hotel_options` normally subtracts every active hold, which is right for an operational
    # read: those rooms are gone. It is wrong here. The re-evaluation is asking what the rules
    # would find for *this* disruption, and the rooms this disruption already secured are part of
    # the answer being re-derived, not a constraint on deriving it. Leaving them subtracted made
    # the baseline allocate nothing and report the entire requirement as short — 166 against Blast
    # Radius's 95, for the same group, on the same screen, both rendered as fact.
    #
    # The demand side already had this fix (`_accommodation_basis` reads the recorded
    # requirement rather than recomputing one); the supply side had simply not been carried across.
    allocation = allocate_rooms(
        passengers=await _accommodation_basis(session, baseline=baseline),
        options=await load_hotel_options(
            session,
            airport_icao=baseline.airport_icao,
            excluding_group_id=await _group_id_for(session, baseline=baseline),
        ),
        constraints=constraints,
    )

    return {
        "connections_at_risk": connections_at_risk,
        "crew_pairings_affected": crew_pairings_affected,
        "rooms_required": allocation.rooms_required,
        "rooms_short": allocation.shortfall_rooms,
    }


async def _group_id_for(session: AsyncSession, *, baseline: CascadeRollup) -> int | None:
    """The disruption group behind a rollup, found through its member flights' incidents.

    `CascadeRollup` carries the group's reference and its member flight ids but not its id, and
    this is the only place that needs the id. Resolved the same way `_accommodation_basis` resolves
    the incidents, so the two cannot end up talking about different groups.
    """
    from app.models.workflow import Incident

    return (
        await session.execute(
            select(Incident.group_id)
            .where(Incident.flight_id.in_(baseline.member_flight_ids or [0]))
            .where(Incident.group_id.isnot(None))
            .limit(1)
        )
    ).scalar_one_or_none()


async def _accommodation_basis(session: AsyncSession, *, baseline: CascadeRollup) -> int:
    """Passengers needing a room, from the recorded allocations rather than recomputed.

    `rooms_required * passengers_per_room` recovers the demand the live services already
    established. Falls back to zero when no allocation has run: a what-if must not invent a
    demand figure that no service produced.
    """
    from app.db.scenario_queries import load_business_constraints, recorded_actions
    from app.models.enums import ActionType
    from app.models.workflow import Incident
    from app.services.hotel import load_constraints

    incident_ids = [
        int(row[0])
        for row in (
            await session.execute(
                select(Incident.id).where(
                    Incident.group_id.in_(
                        select(Incident.group_id).where(
                            Incident.flight_id.in_(baseline.member_flight_ids or [0])
                        )
                    )
                )
            )
        ).all()
    ]
    rows = await recorded_actions(
        session,
        incident_ids,
        ActionType.reserve_hotel_block.value,
        statuses=("success", "needs_human"),
    )
    if not rows:
        return 0
    per_room = load_constraints(await load_business_constraints(session)).passengers_per_room
    return sum(int(payload.get("rooms_required") or 0) for _i, _a, payload in rows) * per_room


def what_if_payload(result: WhatIfResult) -> dict[str, Any]:
    """The shape Stream D renders."""
    return {
        "group_reference": result.group_reference,
        "rule_version": result.rule_version,
        "basis": result.basis,
        "wrote_rows": result.wrote_rows,
        "boundary_note": result.boundary_note,
        "headline": result.headline,
        "recorded_baseline": result.recorded_baseline,
        "levers_applied": result.levers_applied,
        "levers_available": ALLOWED_LEVERS,
        "levers_rejected": [item.model_dump(mode="json") for item in result.levers_rejected],
        "deltas": [
            {**delta.model_dump(mode="json"), "delta": delta.delta, "summary": delta.summary}
            for delta in result.deltas
        ],
    }
