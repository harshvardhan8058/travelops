"""Service input fact contracts — STREAM C.

Declares, per action type, exactly which facts a service needs and where each one comes from.

This exists because of a specific failure mode. A service that is handed an incomplete input bag
has two options: guess, or refuse. Guessing produces a plausible number with nothing behind it —
zero broken connections because the itineraries never arrived reads as good news. Refusing is
correct, but a refusal discovered at execution time is a stalled workflow that a controller has
to diagnose.

With a declared contract, Stream A can check completeness *before* dispatch and say which fact is
missing and which table it comes from. The failure moves from runtime to preflight, and its
message names a column instead of a symptom.

Each fact records the table behind it, so "where does this number come from" is answerable
without reading the service. That question is the whole point of the system.

Owner: Stream C. Consumed by Stream A's dispatch preflight and by Stream D's evidence panel.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ActionType


class FactSpec(BaseModel):
    """One input fact: its name, whether it is required, and the rows it derives from."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    #: The loader that produces it, so a missing fact has an address rather than a description.
    source: str
    #: Tables read to produce it.
    tables: tuple[str, ...]
    required: bool = True
    note: str = ""


class ServiceInputSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action_type: str
    service: str
    facts: tuple[FactSpec, ...]
    #: What the service will NOT do with these inputs. Stated in the contract so a scope
    #: boundary is discoverable without reading a docstring, and so widening one is a visible
    #: change to a declared surface.
    scope_exclusions: tuple[str, ...] = ()

    @property
    def required_facts(self) -> tuple[str, ...]:
        return tuple(fact.name for fact in self.facts if fact.required)

    @property
    def optional_facts(self) -> tuple[str, ...]:
        return tuple(fact.name for fact in self.facts if not fact.required)

    def missing_from(self, supplied: dict[str, Any]) -> list[FactSpec]:
        """Required facts absent from an input bag. `None` counts as absent, `[]` does not.

        An empty list is a real answer — no itineraries connect — while `None` means nobody
        looked. Conflating the two is how a service ends up reporting zero as a finding.
        """
        return [fact for fact in self.facts if fact.required and supplied.get(fact.name) is None]

    def explain_missing(self, supplied: dict[str, Any]) -> str:
        missing = self.missing_from(supplied)
        if not missing:
            return f"All required facts present for {self.action_type}."
        parts = [
            f"{fact.name} (from {fact.source}, over {', '.join(fact.tables)})" for fact in missing
        ]
        return (
            f"{self.action_type} cannot run. Missing: {'; '.join(parts)}. "
            "Running without these would produce a number with no rows behind it."
        )


_CONSTRAINTS = FactSpec(
    name="business_constraints",
    source="app.db.scenario_queries.load_business_constraints",
    tables=("business_constraint",),
    required=False,
    note="Absent rows fall back to documented defaults, and the result says which it used.",
)


SERVICE_INPUT_SPECS: dict[str, ServiceInputSpec] = {
    ActionType.check_connections.value: ServiceInputSpec(
        action_type=ActionType.check_connections.value,
        service="connection",
        facts=(
            FactSpec(
                name="itineraries",
                source="app.db.scenario_queries.load_connection_inputs",
                tables=("booking", "booking_segment", "passenger"),
            ),
            FactSpec(
                name="flights",
                source="app.db.scenario_queries.load_connection_inputs",
                tables=("flight",),
                note="Every flight the itineraries touch, not only the disrupted one.",
            ),
            FactSpec(
                name="affected_flight_ids",
                source="orchestrator target_refs",
                tables=(),
                required=False,
                note="Scopes the walk to one incident. Absent means walk everything.",
            ),
            _CONSTRAINTS,
        ),
        scope_exclusions=(
            "No seat availability: alternatives are schedule feasible only.",
            "No rebooking decision is made or implied.",
        ),
    ),
    ActionType.assess_crew_impact.value: ServiceInputSpec(
        action_type=ActionType.assess_crew_impact.value,
        service="crew_impact",
        facts=(
            FactSpec(
                name="affected_flights",
                source="app.db.scenario_queries.load_crew_impact_inputs",
                tables=("flight", "incident_group_flight"),
            ),
            FactSpec(
                name="pairings",
                source="app.db.scenario_queries.load_crew_impact_inputs",
                tables=("pairing", "pairing_leg"),
            ),
            FactSpec(
                name="flights",
                source="app.db.scenario_queries.load_crew_impact_inputs",
                tables=("flight",),
            ),
            _CONSTRAINTS,
        ),
        scope_exclusions=(
            "Duty-time legality is never validated: crew_member.duty_hours_limit is not read.",
            "No replacement roster is generated.",
        ),
    ),
    ActionType.find_hotel_options.value: ServiceInputSpec(
        action_type=ActionType.find_hotel_options.value,
        service="hotel_search",
        facts=(
            FactSpec(
                name="hotel_options",
                source="app.services.hotel.load_hotel_options",
                tables=("hotel", "hotel_inventory_hold"),
                note="Availability is total_rooms minus active holds, never hotel.available_rooms.",
            ),
            FactSpec(
                name="passengers",
                source="app.db.scenario_queries.cascade_rollup",
                tables=("booking", "booking_segment"),
            ),
            _CONSTRAINTS,
        ),
        scope_exclusions=("A read. Nothing is held and no property becomes unavailable.",),
    ),
    ActionType.reserve_hotel_block.value: ServiceInputSpec(
        action_type=ActionType.reserve_hotel_block.value,
        service="hotel_allocation",
        facts=(
            FactSpec(
                name="hotel_options",
                source="app.services.hotel.load_hotel_options",
                tables=("hotel", "hotel_inventory_hold"),
            ),
            FactSpec(
                name="passengers",
                source="app.services.passenger_impact needing_accommodation",
                tables=("passenger_impact", "booking"),
            ),
            _CONSTRAINTS,
        ),
        scope_exclusions=(
            "Capacity inside the rate cap is short by design: a partial allocation escalates "
            "with a named shortfall rather than silently exceeding the cap.",
        ),
    ),
    ActionType.notify_passengers.value: ServiceInputSpec(
        action_type=ActionType.notify_passengers.value,
        service="communication",
        facts=(
            FactSpec(
                name="recipients",
                source="app.db.scenario_queries.load_connection_inputs",
                tables=("passenger", "booking"),
            ),
            _CONSTRAINTS,
        ),
        scope_exclusions=("Nothing is sent to a real address: notifications are simulated.",),
    ),
}

#: Passenger impact has no ActionType of its own yet — Stream A owns the enum. Keyed by service
#: name so the contract is published now and the enum entry can be added without a second
#: source of truth appearing in the meantime.
SERVICE_INPUT_SPECS_BY_SERVICE: dict[str, ServiceInputSpec] = {
    "passenger_impact": ServiceInputSpec(
        action_type="passenger_impact",
        service="passenger_impact",
        facts=(
            FactSpec(
                name="cohort_facts",
                source="app.services.passenger_impact.PassengerCohortFacts",
                tables=("passenger", "booking", "booking_segment", "action"),
                note=(
                    "connection_broken comes from the recorded Connection action, not "
                    "recomputed: one service owns one fact."
                ),
            ),
            _CONSTRAINTS,
        ),
        scope_exclusions=(
            "No seat availability, no party or PNR grouping, no special-needs sub-categories, "
            "no monetary valuation of a passenger. The schema carries none of these.",
        ),
    ),
    **{spec.service: spec for spec in SERVICE_INPUT_SPECS.values()},
}


def required_facts_for(action_type: str) -> tuple[str, ...]:
    """Required fact names for an action type. Empty tuple when none is declared."""
    spec = SERVICE_INPUT_SPECS.get(action_type)
    return spec.required_facts if spec else ()


def spec_for(action_type: str) -> ServiceInputSpec | None:
    return SERVICE_INPUT_SPECS.get(action_type)


def missing_facts_for(action_type: str, supplied: dict[str, Any]) -> list[FactSpec]:
    """Required facts an input bag does not carry. Empty when the action is undeclared.

    Undeclared returns empty rather than raising: a contract that has not been written yet must
    not block dispatch of a service that was working before the contract existed.
    """
    spec = SERVICE_INPUT_SPECS.get(action_type)
    return spec.missing_from(supplied) if spec else []


class ContractCatalogue(BaseModel):
    """The whole surface, for the API to publish so Stream A and D read one source."""

    model_config = ConfigDict(extra="forbid")

    specs: list[ServiceInputSpec] = Field(default_factory=list)

    @classmethod
    def build(cls) -> ContractCatalogue:
        return cls(specs=[SERVICE_INPUT_SPECS[key] for key in sorted(SERVICE_INPUT_SPECS)])

    def payload(self) -> dict[str, Any]:
        return {
            "specs": [spec.model_dump(mode="json") for spec in self.specs],
            "note": (
                "Declared inputs per action type. A service missing a required fact refuses "
                "rather than reporting a figure no rows support."
            ),
        }
