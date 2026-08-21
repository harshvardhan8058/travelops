"""Jurisdiction resolver — STREAM B.

Maps a trip context to the applicable policy pack(s). Applicability is TRI-STATE:

    applicable | not_applicable | undetermined

A missing required fact yields `undetermined`, never `not_applicable`. Collapsing unknown
into false is how a system accidentally denies a passenger an entitlement.

The tri-state runs all the way down into the condition logic, not just the fact checklist. If
one applicability condition is satisfied the pack applies; if none is satisfied but one is
unknown, the answer is `undetermined`. Only when every condition is definitively false is the
pack `not_applicable`.

No global "most favourable to the passenger" rule is assumed. Where two packs overlap and no
reviewed conflict rule exists, the result is needs_human.
"""

from __future__ import annotations

from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from app.assurance.checks import dedupe
from app.models.enums import ApplicabilityStatus
from app.policy.engine import UNKNOWN, ApplicabilityResult, absent_facts, evaluate_condition
from app.policy.loader import LoadedPack

RESOLVER_VERSION = "resolver-v1"

#: Returned as the decision when a caller must intervene before any entitlement is computed.
NEEDS_HUMAN: Final = "needs_human"
PROCEED: Final = "proceed"

#: Reason codes for a resolution that cannot proceed.
REASON_MISSING_REQUIRED_FACT: Final = "MISSING_REQUIRED_FACT"
REASON_UNDETERMINED_APPLICABILITY: Final = "UNDETERMINED_APPLICABILITY"
REASON_NO_APPLICABLE_PACK: Final = "NO_APPLICABLE_PACK"
REASON_UNRESOLVED_PACK_OVERLAP: Final = "UNRESOLVED_PACK_OVERLAP"


class Resolution(BaseModel):
    """The resolver's decision across every candidate pack.

    Mirrors the resolver output documented in docs/19-jurisdiction-and-policy-packs.md.
    """

    model_config = ConfigDict(extra="forbid")

    candidates: list[ApplicabilityResult] = Field(default_factory=list)
    selected: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    decision: str = NEEDS_HUMAN
    blocking_reasons: list[str] = Field(default_factory=list)
    missing_facts: list[str] = Field(default_factory=list)
    resolver_version: str = RESOLVER_VERSION

    @property
    def requires_human(self) -> bool:
        return self.decision == NEEDS_HUMAN


def _applicability_conditions(pack: LoadedPack) -> dict[str, Any]:
    """Normalise `applies_when` into the same node shape the rule engine evaluates.

    applicability.yaml writes conditions as `- itinerary.origin_country: IN`, which is a
    mapping of fact path to expected value rather than the `{fact, op, value}` leaf the engine
    uses. Translating here keeps one condition evaluator for the whole of policy.
    """
    raw = pack.applies_when or {}

    def _leaves(entries: Any) -> list[dict[str, Any]]:
        leaves: list[dict[str, Any]] = []
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            if "fact" in entry:
                leaves.append(entry)
                continue
            for path, expected in entry.items():
                leaves.append({"fact": str(path), "op": "eq", "value": expected})
        return leaves

    for key in ("any_of", "any"):
        if key in raw:
            return {"any_of": _leaves(raw[key])}
    if "all" in raw:
        return {"all": _leaves(raw["all"])}
    if "all_of" in raw:
        return {"all": _leaves(raw["all_of"])}
    return {}


def resolve(*, trip_context: dict[str, Any], packs: list[LoadedPack]) -> list[ApplicabilityResult]:
    """Return one applicability result per candidate pack, with its basis and gaps.

    A pack is `undetermined` when a fact it declares required is absent, or when its
    applicability conditions cannot be decided without a fact that is absent. It is
    `not_applicable` only when its conditions are definitively false on facts we actually
    have.
    """
    results: list[ApplicabilityResult] = []

    for pack in packs:
        missing_required = absent_facts(trip_context, list(pack.required_facts))

        if missing_required:
            results.append(
                ApplicabilityResult(
                    status=ApplicabilityStatus.undetermined,
                    pack_id=pack.pack_id,
                    pack_version=pack.version,
                    basis={"declared_required_facts_absent": missing_required},
                    required_facts=list(pack.required_facts),
                    missing_facts=missing_required,
                )
            )
            continue

        conditions = _applicability_conditions(pack)
        if not conditions:
            # A pack that declares no applicability conditions cannot be shown to apply.
            results.append(
                ApplicabilityResult(
                    status=ApplicabilityStatus.undetermined,
                    pack_id=pack.pack_id,
                    pack_version=pack.version,
                    basis={"reason": "pack declares no applicability conditions"},
                    required_facts=list(pack.required_facts),
                )
            )
            continue

        outcome, unknown_paths, matched = evaluate_condition(conditions, trip_context)

        if outcome is True:
            status = ApplicabilityStatus.applicable
        elif outcome is UNKNOWN:
            status = ApplicabilityStatus.undetermined
        else:
            status = ApplicabilityStatus.not_applicable

        results.append(
            ApplicabilityResult(
                status=status,
                pack_id=pack.pack_id,
                pack_version=pack.version,
                basis=matched if status is ApplicabilityStatus.applicable else {},
                required_facts=list(pack.required_facts),
                missing_facts=dedupe(unknown_paths)
                if status is not ApplicabilityStatus.applicable
                else [],
            )
        )

    return results


def select(*, trip_context: dict[str, Any], packs: list[LoadedPack]) -> Resolution:
    """Resolve applicability and decide whether evaluation may proceed.

    Blocks when:
      * a required fact is absent, or applicability is otherwise undetermined
      * no pack applies — we have no reviewed rules for this itinerary, which is not the same
        as nothing being owed
      * more than one pack applies and no reviewed conflict rule exists

    That last case is the one worth being strict about. Picking the more generous pack would
    look helpful and would be an unreviewed legal judgement.
    """
    candidates = resolve(trip_context=trip_context, packs=packs)
    by_id = {pack.pack_id: pack for pack in packs}

    applicable = [c for c in candidates if c.status is ApplicabilityStatus.applicable]
    undetermined = [c for c in candidates if c.status is ApplicabilityStatus.undetermined]

    missing = dedupe([path for candidate in candidates for path in candidate.missing_facts])

    if undetermined:
        reasons = [REASON_UNDETERMINED_APPLICABILITY]
        if missing:
            reasons.insert(0, REASON_MISSING_REQUIRED_FACT)
        return Resolution(
            candidates=candidates,
            decision=NEEDS_HUMAN,
            blocking_reasons=reasons,
            missing_facts=missing,
        )

    if not applicable:
        return Resolution(
            candidates=candidates,
            decision=NEEDS_HUMAN,
            blocking_reasons=[REASON_NO_APPLICABLE_PACK],
        )

    if len(applicable) > 1:
        overlapping = [c.pack_id for c in applicable]
        unreviewed = [
            pack_id for pack_id in overlapping if not by_id[pack_id].conflict_rules_defined
        ]
        if unreviewed:
            return Resolution(
                candidates=candidates,
                conflicts=overlapping,
                decision=NEEDS_HUMAN,
                blocking_reasons=[REASON_UNRESOLVED_PACK_OVERLAP],
            )

    return Resolution(
        candidates=candidates,
        selected=[c.pack_id for c in applicable],
        decision=PROCEED,
    )
