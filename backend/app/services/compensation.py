"""Compensation service — the law lives in Stream B's policy engine.

This service assembles the required facts and calls `app.policy.entitlements.calculate`. It
**never** computes an entitlement itself and never infers a legal outcome from `trigger_type`.

Two properties matter more than anything else here:

* **No arithmetic.** The cash figure, the formula and its rendered derivation all come back from
  the engine. If this module ever computed a rupee value, the citation on screen would no longer
  describe the number beside it.
* **A missing fact is `needs_human`, not a default.** `calculate` never raises for an absent
  fact; it returns an outcome naming what was missing. Defaulting a fare to zero would silently
  turn "we do not know" into "nothing is owed", which is the single most damaging thing this
  service could do.

The result is passed through unchanged, including its pack status, so a charter-mode figure can
never be presented as current law by the time it reaches a screen.

Owner: Stream D (fact gathering) / Stream B (the law).
"""

from __future__ import annotations

from typing import Any

from app.models.enums import ActionStatus
from app.policy.entitlements import CitedEntitlement, calculate
from app.schemas.provenance import ProvenanceKind
from app.services.base import ServiceResult

#: Facts the engine needs before it can evaluate anything at all. Absent ones are reported by
#: name rather than substituted, because "which fact was missing" is what an operator acts on.
REQUIRED_FACT_ROOTS: tuple[str, ...] = ("event", "flight")


class CompensationService:
    name = "compensation"

    async def execute(self, **kwargs: Any) -> ServiceResult:
        facts = kwargs.get("facts")
        if not isinstance(facts, dict) or not facts:
            return ServiceResult(
                status=ActionStatus.needs_human,
                reason=(
                    "Entitlement evaluation needs the trip context. Evaluating with no facts "
                    "would produce a lawful-looking 'nothing owed', which is a claim about a "
                    "passenger's rights that nothing supports."
                ),
                payload={"reason_code": "POLICY_FACTS_UNAVAILABLE"},
                provenance_kind=ProvenanceKind.unavailable.value,
            )

        missing_roots = [root for root in REQUIRED_FACT_ROOTS if not facts.get(root)]
        if missing_roots:
            return ServiceResult(
                status=ActionStatus.needs_human,
                reason=(
                    "Entitlement evaluation is missing "
                    + ", ".join(missing_roots)
                    + "; the pack cannot be applied without them."
                ),
                payload={
                    "reason_code": "POLICY_FACTS_INCOMPLETE",
                    "missing_facts": missing_roots,
                },
                provenance_kind=ProvenanceKind.unavailable.value,
            )

        cited: CitedEntitlement = calculate(facts=facts, pack=kwargs.get("pack"))
        payload = _payload(cited)

        if cited.requires_human:
            return ServiceResult(
                status=ActionStatus.needs_human,
                reason=(
                    "The pack could not be applied to these facts: "
                    + (
                        ", ".join(cited.blocking_reasons or cited.missing_facts)
                        or "no reason recorded"
                    )
                ),
                payload=payload,
                evidence_refs=list(cited.source_clause_refs),
                provenance_kind=ProvenanceKind.unavailable.value,
            )

        return ServiceResult(
            status=ActionStatus.success,
            reason=_reason(cited),
            payload=payload,
            # Clause references, so the figure is defensible from the record alone.
            evidence_refs=list(cited.source_clause_refs),
            provenance_kind=ProvenanceKind.synthetic.value,
        )


def _reason(cited: CitedEntitlement) -> str:
    """A sentence that carries the figure, its derivation and its legal standing together.

    Deliberately one string. Splitting the amount from the pack status invites a caller to
    render the amount and drop the caveat.
    """
    if cited.cash_inr is None:
        return (
            "No monetary compensation arises under "
            f"{cited.pack_ui_label or cited.pack_id or 'the active pack'}"
            + (f": {cited.formula_used}" if cited.formula_used else "")
        )
    standing = "current law" if cited.may_be_presented_as_current_law else "dated official guidance"
    return (
        f"{cited.currency or 'INR'} {cited.cash_inr} under "
        f"{cited.pack_ui_label or cited.pack_id} ({standing})"
        + (f": {cited.formula_used}" if cited.formula_used else "")
    )


def _payload(cited: CitedEntitlement) -> dict[str, Any]:
    """The engine's own output, verbatim.

    `model_dump` rather than a hand-picked subset: a caller that needs the excluded rules or the
    undetermined ones should not have to wait for this function to be extended, and a subset is
    how a citation loses the clause that qualifies it.
    """
    return {
        **cited.model_dump(mode="json"),
        "computed_by": "app.policy.entitlements.calculate",
        "scope_note": (
            "Computed by the policy engine from the cited pack. This service gathered facts and "
            "performed no arithmetic."
        ),
    }
