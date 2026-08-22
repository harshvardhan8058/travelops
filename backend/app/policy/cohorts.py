"""Network-scale entitlement evaluation — STREAM B, new in Phase 2.

A cascade touches many passengers across many flights. Evaluating one passenger at a time would be
slow, non-deterministic in cost and impossible to cite in a UI. So passengers are grouped into
**cohorts** by the facts that determine their entitlement, and each distinct fact signature is
evaluated once.

174 passengers become three cited results: *"three cohorts, ₹5,000 each where the notice obligation
was unmet"*.

**The fail-closed rule that matters most here.** `exposure_inr` is `None` unless EVERY cohort
resolved. A partial total presented as a total is worse than no total: it looks authoritative and
under-reports, and the plan-level exposure check would then compare a real budget against a number
that is missing an unknown amount. One unresolved cohort makes the network exposure unknown, which
makes `exposure_within_limits` fail.

Stream B never derives a cohort or counts a passenger. `passenger_count` and the cohort split arrive
from Stream C's records; this module evaluates the facts it is handed and does the arithmetic.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.assurance.checks import dedupe
from app.config import Settings
from app.policy.entitlements import CitedEntitlement, calculate
from app.policy.loader import LoadedPack


class CohortRequest(BaseModel):
    """One group of passengers who share the facts that decide their entitlement."""

    model_config = ConfigDict(extra="forbid")

    cohort_id: str
    facts: dict[str, Any]
    #: Stream C's figure. Stream B never counts passengers.
    passenger_count: int
    #: Optional label for the UI, e.g. "missed connection, same ticket".
    label: str | None = None

    def signature(self) -> str:
        """Identity of the facts, so two cohorts with identical facts evaluate once.

        Canonical JSON, so key order in the caller's dictionary cannot produce two signatures for
        one set of facts.
        """
        canonical = json.dumps(self.facts, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


class CohortEntitlement(BaseModel):
    """One cohort's cited result, with the arithmetic that scales it to the cohort."""

    model_config = ConfigDict(extra="forbid")

    cohort_id: str
    label: str | None = None
    signature: str
    passenger_count: int
    entitlement: CitedEntitlement
    #: per-passenger cash x passenger_count, or None when the cohort is unresolved.
    cohort_exposure_inr: int | None = None

    @property
    def requires_human(self) -> bool:
        return self.entitlement.requires_human


class CohortEntitlementSet(BaseModel):
    """Network-level entitlement result for one plan or group."""

    model_config = ConfigDict(extra="forbid")

    cohorts: list[CohortEntitlement] = Field(default_factory=list)
    #: None whenever any cohort is unresolved. Never a partial sum.
    exposure_inr: int | None = None
    passengers_covered: int = 0
    unresolved_cohorts: list[str] = Field(default_factory=list)

    pack_id: str | None = None
    pack_version: str | None = None
    pack_hash: str | None = None
    pack_status: str | None = None
    policy_mode: str | None = None
    currency: str | None = None
    #: One evaluation per distinct fact signature, however many cohorts shared it.
    evaluations_performed: int = 0

    @property
    def requires_human(self) -> bool:
        return bool(self.unresolved_cohorts)

    @property
    def exposure_established(self) -> bool:
        return self.exposure_inr is not None


def calculate_cohorts(
    *,
    cohorts: list[CohortRequest],
    pack: LoadedPack,
    settings: Settings | None = None,
) -> CohortEntitlementSet:
    """Evaluate every cohort, reusing one evaluation per distinct fact signature.

    Deterministic: identical input produces an identical result, and two cohorts with identical
    facts produce identical figures because they are the same evaluation.

    Never raises for a missing fact. An unresolved cohort is reported, its figure withheld, and the
    network exposure suppressed.
    """
    if not cohorts:
        return CohortEntitlementSet(
            pack_id=pack.pack_id,
            pack_version=pack.version,
            pack_hash=pack.pack_hash,
            pack_status=pack.status.value,
            currency=pack.currency,
        )

    by_signature: dict[str, CitedEntitlement] = {}
    evaluated: list[CohortEntitlement] = []
    unresolved: list[str] = []
    running_total = 0
    passengers = 0

    for cohort in cohorts:
        signature = cohort.signature()

        cited = by_signature.get(signature)
        if cited is None:
            cited = calculate(facts=cohort.facts, pack=pack, settings=settings)
            by_signature[signature] = cited

        exposure: int | None = None
        if cited.requires_human or cited.cash_inr is None:
            unresolved.append(cohort.cohort_id)
        else:
            exposure = cited.cash_inr * cohort.passenger_count
            running_total += exposure
            passengers += cohort.passenger_count

        evaluated.append(
            CohortEntitlement(
                cohort_id=cohort.cohort_id,
                label=cohort.label,
                signature=signature,
                passenger_count=cohort.passenger_count,
                entitlement=cited,
                cohort_exposure_inr=exposure,
            )
        )

    first = evaluated[0].entitlement

    return CohortEntitlementSet(
        cohorts=evaluated,
        # Withheld entirely while any cohort is unresolved. A partial total would read as complete.
        exposure_inr=None if unresolved else running_total,
        passengers_covered=passengers,
        unresolved_cohorts=dedupe(unresolved),
        pack_id=first.pack_id,
        pack_version=first.pack_version,
        pack_hash=first.pack_hash,
        pack_status=first.pack_status,
        policy_mode=first.policy_mode,
        currency=first.currency or pack.currency,
        evaluations_performed=len(by_signature),
    )


def exposure_inputs_from(
    result: CohortEntitlementSet,
    *,
    rooms_committed: int | None = None,
    external_effects: int | None = None,
) -> dict[str, Any]:
    """Shape a cohort result as plan-level exposure inputs.

    `rooms_committed` and `external_effects` are Stream C's and Stream A's figures respectively and
    are passed straight through — this function does not derive them, and omitting one leaves it
    `None`, which the exposure check treats as a breach rather than as zero.
    """
    return {
        "total_exposure_inr": result.exposure_inr,
        "passengers_affected": result.passengers_covered if result.exposure_established else None,
        "rooms_committed": rooms_committed,
        "external_effects": external_effects,
        "unresolved_cohorts": list(result.unresolved_cohorts),
    }
