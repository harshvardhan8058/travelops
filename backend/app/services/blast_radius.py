"""Blast radius — STREAM C (C2-8).

Composition only. Every figure here is produced by another service and repeated; nothing is
calculated, estimated or inferred. If a number is not in this module's inputs, it is not in its
output.

That restraint is the design. "Blast radius" is the kind of phrase that attracts invented
metrics — an impact score, a severity multiplier, a confidence percentage — and each one would
be a number with no rows behind it sitting next to numbers that have them. Once one figure on
the screen cannot be traced, the credibility of the traceable ones goes with it.

So this module reports **completeness**, never confidence. Completeness is countable: six of
eight flights assessed. Confidence would be a probability, and nothing in this system is
calibrated against observed outcomes, so a confidence figure would be a decoration.

The gap between what is known and what is not is stated in the same breath as the totals, so a
partial answer can never be mistaken for a complete one.

Owner: Stream C.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.db.scenario_queries import CascadeRollup
from app.services.cascade_graph import CascadeGraph


class BlastRadiusDimension(BaseModel):
    """One measured dimension, with the service that measured it named."""

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    value: int
    unit: str
    #: Which service recorded this. Empty means declared data rather than a service finding.
    measured_by: str
    #: True when every flight in the group contributed. False makes the value a floor.
    is_complete: bool
    note: str = ""

    @property
    def qualifier(self) -> str:
        return "" if self.is_complete else " (at least)"

    @property
    def headline_noun(self) -> str:
        """A short noun for the headline: the unit, unless two dimensions would share one.

        `rooms_required` and `rooms_short` are both measured in rooms, so the unit alone cannot tell
        them apart in a sentence. Taken from the label rather than hardcoded per key, so a new
        dimension is described by its own label instead of inheriting a mapping nobody updated.
        """
        if self.unit == "rooms":
            return self.label.lower()
        if self.unit == "INR":
            return "INR committed"
        return self.unit


class BlastRadius(BaseModel):
    """The composed picture. No dimension originates here."""

    model_config = ConfigDict(extra="forbid")

    group_reference: str
    dimensions: list[BlastRadiusDimension] = Field(default_factory=list)

    flights_declared: int = 0
    flights_assessed: int = 0
    #: Named, countable gaps. Not a score.
    gaps: list[str] = Field(default_factory=list)

    #: Type-level guarantee, mirroring Stream A's `recorded_evidence`. A `confidence` field
    #: cannot be added without changing this literal, which forces the conversation.
    basis: Literal["composed_from_recorded_findings"] = "composed_from_recorded_findings"

    @property
    def is_complete(self) -> bool:
        return self.flights_declared > 0 and self.flights_assessed == self.flights_declared

    @property
    def completeness_ratio(self) -> str:
        return f"{self.flights_assessed}/{self.flights_declared}"

    @property
    def headline(self) -> str:
        """The sentence for the top of the screen, with its own caveat attached.

        Deliberately one string containing both the totals and the qualification. Splitting them
        into separate fields invites a UI that renders the first and drops the second.

        Each figure carries its dimension's own short name rather than its bare unit. Units alone
        produced "303 rooms, 232 rooms" — two different quantities reading as the same one, which is
        exactly the kind of ambiguity that makes a reviewer stop trusting the row.
        """
        parts = [
            f"{dimension.value} {dimension.headline_noun}{dimension.qualifier}"
            for dimension in self.dimensions
            if dimension.value
        ]
        summary = ", ".join(parts) if parts else "nothing recorded yet"
        if self.is_complete:
            return f"{summary}. All {self.flights_declared} declared flights assessed."
        return (
            f"{summary}. Assessed {self.completeness_ratio} declared flights, so these are "
            "floors rather than totals."
        )

    def value_of(self, key: str) -> int:
        for dimension in self.dimensions:
            if dimension.key == key:
                return dimension.value
        return 0


def compose_blast_radius(
    *,
    rollup: CascadeRollup,
    graph: CascadeGraph | None = None,
    passenger_payload: dict[str, Any] | None = None,
    hotel_payload: dict[str, Any] | None = None,
) -> BlastRadius:
    """Assemble the dimensions from what the services already recorded.

    Every argument is another component's output. `passenger_payload` and `hotel_payload` are the
    service payloads verbatim rather than re-derived values, so a figure here and the same figure
    on the action detail can never disagree — there is only one place it was computed.
    """
    complete = rollup.is_complete
    dimensions: list[BlastRadiusDimension] = [
        BlastRadiusDimension(
            key="flights",
            label="Flights in the cascade",
            value=rollup.flights_affected,
            unit="flights",
            measured_by="incident_group_flight",
            is_complete=True,
            note=(
                "Declared membership, so this figure is exact the moment the group exists and "
                "does not depend on how much work has been done."
                if rollup.membership_is_declared
                else "Derived from open incidents; this group declares no membership rows."
            ),
        ),
        BlastRadiusDimension(
            key="passengers",
            label="Passengers on those flights",
            value=rollup.passengers_affected,
            unit="passengers",
            measured_by="booking_segment",
            is_complete=True,
            note="Counted from booking rows against the declared flights.",
        ),
        BlastRadiusDimension(
            key="connections",
            label="Connections that break",
            value=rollup.connections_at_risk,
            unit="connections",
            measured_by="connection",
            is_complete=complete,
            note=(
                "The union of distinct bookings, so a passenger touched by two incidents is "
                "counted once."
            ),
        ),
        BlastRadiusDimension(
            key="crew_pairings",
            label="Crew rotations at risk",
            value=rollup.crew_pairings_affected,
            unit="rotations",
            measured_by="crew_impact",
            is_complete=complete,
            note="Direct impacts only. Second-order expansion is reported separately.",
        ),
        BlastRadiusDimension(
            key="candidate_hotels",
            label="Hotels within search range",
            value=rollup.candidate_hotels,
            unit="hotels",
            measured_by="hotel",
            is_complete=True,
            note="Properties at the airport. A search space, not an allocation.",
        ),
    ]

    if passenger_payload:
        by_band = passenger_payload.get("count_by_band") or {}
        elevated = sum(
            int(count) for band, count in by_band.items() if band in ("critical", "high")
        )
        dimensions.append(
            BlastRadiusDimension(
                key="passengers_elevated",
                label="Passengers ranked high or critical",
                value=elevated,
                unit="passengers",
                measured_by="passenger_impact",
                is_complete=complete,
                note=(
                    "Constraint ranking under ruleset "
                    f"{passenger_payload.get('ruleset_hash', 'unknown')}. Not a probability."
                ),
            )
        )

    if hotel_payload:
        dimensions.append(
            BlastRadiusDimension(
                key="rooms_required",
                label="Rooms required",
                value=int(hotel_payload.get("rooms_required") or 0),
                unit="rooms",
                measured_by="hotel_allocation",
                is_complete=complete,
                note="Passengers needing overnight accommodation, rounded up.",
            )
        )
        dimensions.append(
            BlastRadiusDimension(
                key="rooms_short",
                label="Rooms short",
                value=int(hotel_payload.get("shortfall_rooms") or 0),
                unit="rooms",
                measured_by="hotel_allocation",
                is_complete=complete,
                note=str(hotel_payload.get("shortfall_note") or ""),
            )
        )
        cost = int(hotel_payload.get("total_cost_inr") or 0)
        if cost:
            dimensions.append(
                BlastRadiusDimension(
                    key="committed_cost_inr",
                    label="Accommodation cost committed",
                    value=cost,
                    unit="INR",
                    measured_by="hotel_allocation",
                    is_complete=complete,
                    note=(
                        "Rooms actually held multiplied by their recorded nightly rate. Not a "
                        "forecast of total disruption cost — no such figure is computed."
                    ),
                )
            )

    gaps: list[str] = []
    if rollup.flights_without_incident:
        gaps.append(
            f"{len(rollup.flights_without_incident)} declared flights have no incident open: "
            f"{', '.join(f'flight:{fid}' for fid in rollup.flights_without_incident)}."
        )
    missing_connections = rollup.incidents_in_group - rollup.incidents_assessed_connections
    if missing_connections > 0:
        gaps.append(f"{missing_connections} incidents have not had connections assessed.")
    missing_crew = rollup.incidents_in_group - rollup.incidents_assessed_crew
    if missing_crew > 0:
        gaps.append(f"{missing_crew} incidents have not had crew impact assessed.")
    if graph is not None and not graph.is_complete:
        gaps.append(graph.completeness_note)
    if not passenger_payload:
        gaps.append("Passenger priority has not been assessed for this group.")
    if not hotel_payload:
        gaps.append("No accommodation requirement has been calculated for this group.")

    return BlastRadius(
        group_reference=rollup.group_reference,
        dimensions=dimensions,
        flights_declared=rollup.flights_affected,
        flights_assessed=rollup.incidents_assessed_crew,
        gaps=gaps,
    )


def blast_radius_payload(radius: BlastRadius) -> dict[str, Any]:
    """The shape Stream D renders."""
    return {
        "group_reference": radius.group_reference,
        "headline": radius.headline,
        "basis": radius.basis,
        "dimensions": [dimension.model_dump(mode="json") for dimension in radius.dimensions],
        "completeness": {
            "flights_declared": radius.flights_declared,
            "flights_assessed": radius.flights_assessed,
            "ratio": radius.completeness_ratio,
            "is_complete": radius.is_complete,
        },
        "gaps": radius.gaps,
    }
