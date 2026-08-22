"""Passenger impact intelligence — STREAM C (C2-4).

Answers "who is worst off, and why" from persisted rows only, so that a partial hotel
allocation can be defended rather than merely explained away.

What this is: a **priority index** built from named factors, each traceable to a column or to a
recorded service finding, with the weights held in `business_constraint` rather than in this
file. What it is not: a probability, a prediction, or a statement about whose journey matters
more as a person. It records who is most *constrained* — fewest remaining options, least able
to be reached, most dependent on assistance.

Four things this deliberately does not model, because the data to support them does not exist:

* **Seat availability.** No capacity column exists anywhere in the schema, so "could be
  rebooked" is never asserted. Alternatives elsewhere in the system are schedule-feasible only.
* **Party or PNR grouping.** `booking.pnr` is unique and there is exactly one passenger per
  booking. Inferring families from surnames or adjacent seat numbers would be fabrication.
* **Special-needs sub-categories.** The schema has one boolean. Splitting it into wheelchair,
  medical or unaccompanied minor would invent a distinction the dataset does not carry, and
  those are precisely the categories where being wrong is worst.
* **Monetary value of a passenger.** Tier participates only because an operator put a weight on
  it in `business_constraint`, with an audit trail. This service applies a declared policy; it
  does not decide that loyalty status should matter.

Why the weights live in data: the ranking that decides who gets one of 87 rooms is a policy
question. Held in `business_constraint`, it is inspectable, versionable and hashed into every
record, so a reviewer can see what ruleset produced a given ordering. Hard-coded here, it would
be an engineering opinion presented as arithmetic.

Owner: Stream C.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ActionStatus, PriorityBand, ProvenanceKind
from app.services.base import ServiceResult

RULE_VERSION = "passenger-impact-v1"

BUSINESS_CONSTRAINT_SERVICE = "passenger_impact"
BUSINESS_CONSTRAINT_KEY = "priority_ruleset"

#: The default ruleset, used only when `business_constraint` carries none. Seeded as data, so
#: in the demo path the row is what applies and this constant is the fallback of last resort.
#:
#: Weights are additive and the total is clamped to 100. Clamping rather than normalising is
#: deliberate: a passenger who is simultaneously unreachable, needs assistance and is stranded
#: overnight should saturate the scale. That *is* the top of the scale.
DEFAULT_RULESET: dict[str, Any] = {
    "version": "priority-ruleset-v1",
    "factors": {
        # Their onward segment no longer connects. Recorded by the Connection service.
        "broken_connection": 30,
        # No feasible onward departure remains today, so they need somewhere to sleep.
        "overnight_exposure": 25,
        # `passenger.has_special_needs`. One boolean, used as one boolean.
        "special_needs_recorded": 20,
        # `booking.contact_info_provided_at_booking` is false: no notification can reach them,
        # so they must be found in person. Weighted highly because it is the factor most likely
        # to be forgotten in a room full of people watching a screen.
        "unreachable_contact": 15,
        # Stranded mid-itinerary: not at the origin they started from, not at their final
        # destination. They have nowhere to go back to.
        "journey_incomplete": 10,
    },
    "tier_weights": {"platinum": 10, "gold": 7, "silver": 4, "standard": 0},
    # Lower bound of each band, checked highest first.
    "bands": {"critical": 70, "high": 45, "elevated": 20, "routine": 0},
}


def ruleset_hash(ruleset: dict[str, Any]) -> str:
    """16 hex characters over canonical JSON.

    Stamped onto every `passenger_impact` row so an ordering can always be tied back to the
    policy that produced it. Two runs with different hashes are not comparable, and saying so
    is more useful than silently comparing them.
    """
    encoded = json.dumps(ruleset, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def load_ruleset(rows: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Read the ruleset from `business_constraint`, falling back to the default."""
    for row in rows or []:
        if (
            row.get("service") == BUSINESS_CONSTRAINT_SERVICE
            and row.get("constraint_key") == BUSINESS_CONSTRAINT_KEY
        ):
            value = row.get("constraint_value")
            if isinstance(value, dict) and value.get("factors"):
                return value
    return DEFAULT_RULESET


# ----------------------------------------------------------------------------- inputs


class PassengerCohortFacts(BaseModel):
    """Everything known about one passenger's exposure, all of it from persisted rows.

    A plain value object rather than an ORM row, so the scoring is testable without a database
    and identical whether it runs over the demo dataset or a query result.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    passenger_id: int
    passenger_reference: str
    booking_id: int
    pnr: str
    tier: str = "standard"

    #: `passenger.has_special_needs`.
    has_special_needs: bool = False
    #: `booking.contact_info_provided_at_booking` inverted.
    contact_missing: bool = False

    #: From the recorded Connection action, not recomputed here. One service owns one fact.
    connection_broken: bool = False
    #: True when no schedule-feasible onward departure remains on the day of travel.
    no_onward_option_today: bool = False
    #: True when they are stranded between their origin and their final destination.
    stranded_mid_itinerary: bool = False

    #: The flight in the disruption that this exposure hangs off.
    flight_id: int | None = None


# ---------------------------------------------------------------------------- outputs


class PassengerPriority(BaseModel):
    """One passenger's index, band and the factors that produced it.

    `factors` is the whole justification: every point in `priority_index` is attributable to a
    named entry. A score without them would be a number nobody could argue with, which in an
    operations room is worse than no score.
    """

    model_config = ConfigDict(extra="forbid")

    passenger_id: int
    passenger_reference: str
    booking_id: int
    pnr: str
    priority_index: int
    priority_band: PriorityBand
    factors: list[dict[str, Any]] = Field(default_factory=list)
    flight_id: int | None = None

    @property
    def explanation(self) -> str:
        if not self.factors:
            return "No recorded factor applies, so this passenger sits at the base priority."
        named = ", ".join(f"{item['factor']} (+{item['weight']})" for item in self.factors)
        return f"{self.priority_index}/100, {self.priority_band.value}: {named}."


class PassengerCohort(BaseModel):
    """A band of passengers who need the same kind of handling."""

    model_config = ConfigDict(extra="forbid")

    band: PriorityBand
    passenger_count: int
    booking_ids: list[int] = Field(default_factory=list)
    #: How many passengers in this band carry each factor. The operational shopping list.
    factor_counts: dict[str, int] = Field(default_factory=dict)
    lowest_index: int = 0
    highest_index: int = 0

    #: The type forbids the wrong answer. Mirrors Stream A's `recorded_evidence`.
    basis: Literal["persisted_records"] = "persisted_records"


class PassengerImpactAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_version: str = RULE_VERSION
    ruleset_version: str
    ruleset_hash: str
    priorities: list[PassengerPriority] = Field(default_factory=list)
    cohorts: list[PassengerCohort] = Field(default_factory=list)
    passengers_assessed: int = 0

    @property
    def count_by_band(self) -> dict[str, int]:
        return {cohort.band.value: cohort.passenger_count for cohort in self.cohorts}

    @property
    def needing_accommodation(self) -> list[PassengerPriority]:
        """Passengers with no onward option today, highest priority first.

        The set a hotel allocation draws from, in the order it draws them.
        """
        return [
            priority
            for priority in self.priorities
            if any(item["factor"] == "overnight_exposure" for item in priority.factors)
        ]


# ---------------------------------------------------------------------------- scoring

#: Which fact on `PassengerCohortFacts` switches each factor on. Data, not branches, so adding
#: a factor is a one-line change in two places that cannot disagree.
_FACTOR_SOURCES: tuple[tuple[str, str], ...] = (
    ("broken_connection", "connection_broken"),
    ("overnight_exposure", "no_onward_option_today"),
    ("special_needs_recorded", "has_special_needs"),
    ("unreachable_contact", "contact_missing"),
    ("journey_incomplete", "stranded_mid_itinerary"),
)


def _band_for(index: int, bands: dict[str, Any]) -> PriorityBand:
    """Highest band whose lower bound the index meets. Ties resolve upward."""
    for name in ("critical", "high", "elevated"):
        if index >= int(bands.get(name, 999)):
            return PriorityBand(name)
    return PriorityBand.routine


def score_passenger(
    facts: PassengerCohortFacts, *, ruleset: dict[str, Any] | None = None
) -> PassengerPriority:
    """Score one passenger. Pure, deterministic, and explainable line by line."""
    active = ruleset or DEFAULT_RULESET
    weights = active.get("factors", {})
    factors: list[dict[str, Any]] = []
    total = 0

    for factor, attribute in _FACTOR_SOURCES:
        if not getattr(facts, attribute, False):
            continue
        weight = int(weights.get(factor, 0))
        if weight == 0:
            # Present in the data but given no weight by policy. Skipped rather than recorded
            # with +0, which would read as a factor that mattered and then did nothing.
            continue
        total += weight
        factors.append({"factor": factor, "weight": weight, "source": attribute})

    tier_weight = int(active.get("tier_weights", {}).get(facts.tier.lower(), 0))
    if tier_weight:
        total += tier_weight
        factors.append(
            {"factor": "recorded_tier", "weight": tier_weight, "source": f"tier={facts.tier}"}
        )

    index = min(100, max(0, total))
    return PassengerPriority(
        passenger_id=facts.passenger_id,
        passenger_reference=facts.passenger_reference,
        booking_id=facts.booking_id,
        pnr=facts.pnr,
        priority_index=index,
        priority_band=_band_for(index, active.get("bands", {})),
        factors=factors,
        flight_id=facts.flight_id,
    )


def assess_passenger_impact(
    *,
    cohort_facts: list[PassengerCohortFacts],
    ruleset: dict[str, Any] | None = None,
) -> PassengerImpactAssessment:
    """Score every passenger and group them into bands.

    Ordering is by index descending then passenger id, so a tie never depends on how the caller
    happened to sort its query. That matters here more than elsewhere: this ordering decides who
    gets a room when there are not enough.
    """
    active = ruleset or DEFAULT_RULESET
    priorities = sorted(
        (score_passenger(facts, ruleset=active) for facts in cohort_facts),
        key=lambda item: (-item.priority_index, item.passenger_id),
    )

    cohorts: list[PassengerCohort] = []
    for band in (
        PriorityBand.critical,
        PriorityBand.high,
        PriorityBand.elevated,
        PriorityBand.routine,
    ):
        members = [item for item in priorities if item.priority_band is band]
        if not members:
            continue
        factor_counts: dict[str, int] = {}
        for member in members:
            for entry in member.factors:
                factor_counts[entry["factor"]] = factor_counts.get(entry["factor"], 0) + 1
        cohorts.append(
            PassengerCohort(
                band=band,
                passenger_count=len(members),
                booking_ids=sorted(member.booking_id for member in members),
                factor_counts=dict(sorted(factor_counts.items())),
                lowest_index=min(member.priority_index for member in members),
                highest_index=max(member.priority_index for member in members),
            )
        )

    return PassengerImpactAssessment(
        ruleset_version=str(active.get("version", "unknown")),
        ruleset_hash=ruleset_hash(active),
        priorities=priorities,
        cohorts=cohorts,
        passengers_assessed=len(priorities),
    )


# ---------------------------------------------------------------------------- service


class PassengerImpactService:
    name = "passenger_impact"

    async def execute(self, **kwargs: Any) -> ServiceResult:
        """Rank affected passengers by how constrained they are.

        Inputs:
            cohort_facts:          list[PassengerCohortFacts], built from persisted rows
            business_constraints:  rows supplying the priority ruleset
        """
        cohort_facts = kwargs.get("cohort_facts")

        if cohort_facts is None:
            return ServiceResult(
                status=ActionStatus.needs_human,
                reason=(
                    "Passenger impact needs the cohort facts. Ranking nobody would present "
                    "an empty priority list as though every passenger were equally fine."
                ),
                payload={"rule_version": RULE_VERSION},
                provenance_kind=ProvenanceKind.unavailable.value,
            )

        ruleset = load_ruleset(kwargs.get("business_constraints"))
        assessment = assess_passenger_impact(cohort_facts=list(cohort_facts), ruleset=ruleset)

        evidence = [f"passenger:{item.passenger_id}" for item in assessment.priorities]
        evidence += [f"booking:{item.booking_id}" for item in assessment.priorities]

        return ServiceResult(
            status=ActionStatus.success,
            reason=(
                f"{assessment.passengers_assessed} passengers ranked into "
                f"{len(assessment.cohorts)} cohorts under ruleset "
                f"{assessment.ruleset_version}"
            ),
            payload={
                "rule_version": RULE_VERSION,
                "ruleset_version": assessment.ruleset_version,
                "ruleset_hash": assessment.ruleset_hash,
                "passengers_assessed": assessment.passengers_assessed,
                "count_by_band": assessment.count_by_band,
                "needing_accommodation": len(assessment.needing_accommodation),
                "cohorts": [cohort.model_dump(mode="json") for cohort in assessment.cohorts],
                "priorities": [item.model_dump(mode="json") for item in assessment.priorities],
                "scope_note": (
                    "A constraint ranking from persisted rows, not a probability and not a "
                    "judgement of whose journey matters more. No seat availability, no party "
                    "grouping and no special-needs sub-categories: the schema carries none of "
                    "those, and inventing them here would be fabrication."
                ),
            },
            evidence_refs=sorted(set(evidence)),
            provenance_kind=ProvenanceKind.synthetic.value,
        )
