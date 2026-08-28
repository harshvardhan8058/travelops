"""Incident policy endpoint — STREAM A, G4.

Replaces the fixture route for `GET /incidents/{ref}/policy`. Same path, same shape, real data: the
fixture route is deleted in the same commit, so the path is never served by two implementations.

**Stream A computes no entitlement here.** Every figure comes from Stream B's policy layer through
its existing interfaces:

| Value | Source |
| --- | --- |
| Trip facts | `db.trip_context.load_trip_context` (Stream C) |
| Pack identity, status, label, hash | `policy.entitlements.load_active_pack` (Stream B) |
| Applicability, basis, missing facts | `policy.resolver.select` via `calculate` (Stream B) |
| Entitlements, rules fired, clause refs | `policy.entitlements.calculate` (Stream B) |
| Excluded rules and their reasons | `LoadedPack.excluded_rules` (Stream B) |

This module resolves an incident, gathers facts, calls the engine, and shapes the answer. Nothing
here decides what a passenger is owed.

Three things it will not do, each because the alternative is worse than an empty field:

1. **Never turn absent into zero.** A missing fact yields `undetermined` and is named. `not_owed` is
   a computed result and is reported as one; the two are never merged.
2. **Never assert a legal exemption from an operational label.** `trigger_type: weather` is not
   evidence that a delay was unavoidable despite reasonable measures, so `cause_assessment` reports
   only what is recorded.
3. **Never claim a standing the pack does not have.** The disclaimer is derived from the pack's own
   status and `may_be_presented_as_current_law`.

Owner: Stream A.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.incidents import _load_incident
from app.db.session import get_session
from app.db.trip_context import load_trip_context
from app.errors import EntityNotFound
from app.observability.logging import get_logger
from app.schemas.policy import (
    PolicyApplicability,
    PolicyCauseAlternative,
    PolicyCauseAssessment,
    PolicyCauseComparison,
    PolicyEntitlement,
    PolicyExcludedRule,
    PolicyPackInfo,
    PolicyResponse,
)

router = APIRouter(tags=["policy"])
log = get_logger(__name__)

GENERATED_BY = "policy-engine"

NOTE = (
    "Every figure is computed by the policy engine from the active pack and the recorded trip "
    "facts. Nothing on this response is a stored sample."
)

CAUSE_NOTE = (
    "trigger_type is operational context, never a legal verdict. An exemption requires a recorded "
    "cause assessment; where none exists these flags are undetermined rather than false."
)

COMPARISON_DESCRIPTION = (
    "The same incident re-evaluated under a substituted cause, using the same pack and the same "
    "rules. A bounded re-evaluation of recorded facts, not a forecast, and nothing is written."
)

#: The counterfactual the comparison runs: an internal cause, where no beyond-control exemption can
#: apply. Chosen because it is the one substitution that shows the exemption doing work.
ALTERNATIVE_CAUSE = "crew_rostering"
ALTERNATIVE_EVENT = "cancellation"


def _disclaimer(pack: Any, cited: Any) -> str:
    """Legal standing, derived from the pack rather than written per screen."""
    if getattr(cited, "may_be_presented_as_current_law", False):
        return (
            f"Figures are from {pack.document or pack.pack_id}, an approved and verified source "
            f"({pack.ui_label}). They may be presented as current law."
        )
    return (
        f"Figures are from {pack.document or pack.pack_id} — an official but dated publication "
        f"({pack.ui_label}). They are cited guidance, not current law, and must not be presented "
        "as a final entitlement determination."
    )


def _entitlements(cited: Any) -> list[PolicyEntitlement]:
    """Project the engine's entitlements, preserving the tri-state.

    `cited.entitlements` is the engine's own list. When it is empty the engine still reached an
    outcome — `not_owed`, `needs_human` or `suppressed` — and that outcome is reported as a single
    row rather than as an empty table, because an empty table reads as "nothing to see".
    """
    rows: list[PolicyEntitlement] = []
    for item in cited.entitlements or []:
        rows.append(
            PolicyEntitlement(
                type=str(item.get("type") or "entitlement"),
                outcome=str(item.get("outcome") or cited.outcome),
                amount_inr=item.get("amount_inr"),
                currency=item.get("currency") or cited.currency,
                reason_codes=[str(code) for code in (item.get("reason_codes") or [])],
                explanation=str(item.get("explanation") or ""),
                rules_fired=[str(rule) for rule in (item.get("rules_fired") or [])],
                source_clause_refs=[str(ref) for ref in (item.get("source_clause_refs") or [])],
                formula_used=item.get("formula_used"),
            )
        )
    if rows:
        return rows

    # No itemised entitlement. Report the engine's outcome and its derivation rather than nothing.
    undetermined = bool(cited.missing_facts or cited.blocking_reasons)
    return [
        PolicyEntitlement(
            type="cash",
            outcome="undetermined" if undetermined else cited.outcome,
            amount_inr=cited.cash_inr,
            currency=cited.currency,
            reason_codes=list(cited.cash_reason_codes or []),
            explanation=(
                "The engine could not determine an entitlement because required facts were "
                f"absent: {', '.join(cited.missing_facts)}."
                if cited.missing_facts
                else (
                    "; ".join(cited.blocking_reasons)
                    if cited.blocking_reasons
                    else "No monetary compensation arises for this event under the active pack."
                )
            ),
            rules_fired=list(cited.rules_fired or []),
            source_clause_refs=list(cited.source_clause_refs or []),
            formula_used=cited.formula_used,
        )
    ]


def _cause_assessment(facts: dict[str, Any]) -> PolicyCauseAssessment:
    """Only what the trip context actually recorded.

    `load_trip_context` deliberately does not populate `cause_evidence` from `trigger_type`, so this
    is usually every flag `null`. That is the honest answer and it is what makes the comparison
    below meaningful.
    """
    evidence = facts.get("cause_evidence") or {}
    return PolicyCauseAssessment(
        operational_cause=evidence.get("operational_cause"),
        clearly_attributable=evidence.get("clearly_attributable"),
        external_to_carrier=evidence.get("external_to_carrier"),
        unavoidable_despite_reasonable_measures=evidence.get(
            "unavoidable_despite_reasonable_measures"
        ),
        evidence_refs=[str(ref) for ref in (evidence.get("evidence_refs") or [])],
        note=CAUSE_NOTE,
    )


def _excluded_rules(pack: Any) -> list[PolicyExcludedRule]:
    return [
        PolicyExcludedRule(
            rule_key=rule.id,
            status=rule.status,
            reason=rule.supersession_note or "Withheld by the pack; not evaluated.",
        )
        for rule in pack.excluded_rules
    ]


@router.get(
    "/incidents/{incident_id}/policy",
    response_model=PolicyResponse,
    summary="Cited entitlement evaluation for one incident",
)
async def get_incident_policy(
    incident_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PolicyResponse:
    """Evaluate the active pack against this incident's recorded facts. Read-only.

    Nothing is persisted: G6 (recording the evaluation and clause text) is Stream C's, and until it
    lands this recomputes from the pack on each request. That keeps one source of truth rather than
    a cache that can disagree with it.
    """
    from app.policy.entitlements import calculate, load_active_pack

    incident = await _load_incident(session, incident_id)
    if incident.flight_id is None:
        raise EntityNotFound(
            "this incident has no flight, so there is no itinerary to evaluate",
            details={"incident_reference": incident.reference},
        )

    facts = await load_trip_context(session, [incident.flight_id])
    pack = load_active_pack()
    cited = calculate(facts=facts, pack=pack)

    applicability = [
        PolicyApplicability(
            pack_id=str(item.get("pack_id") or pack.pack_id),
            pack_version=str(item.get("pack_version") or pack.version),
            status=str(item.get("status") or "undetermined"),
            basis=dict(item.get("basis") or {}),
            required_facts=[str(fact) for fact in (item.get("required_facts") or [])],
            missing_facts=[str(fact) for fact in (item.get("missing_facts") or [])],
            resolver_version=cited.resolver_version,
        )
        for item in (cited.applicability or [])
    ]

    log.info(
        "incident_policy_evaluated",
        incident_reference=incident.reference,
        pack_id=cited.pack_id,
        pack_version=cited.pack_version,
        outcome=cited.outcome,
        missing_facts=len(cited.missing_facts or []),
    )

    return PolicyResponse(
        generated_by=GENERATED_BY,
        note=NOTE,
        policy_mode=cited.policy_mode,
        pack=PolicyPackInfo(
            id=pack.pack_id,
            version=pack.version,
            status=getattr(pack.status, "value", str(pack.status)),
            verified_mode_eligible=pack.verified_mode_eligible,
            ui_label=pack.ui_label,
            authority=pack.authority,
            document=pack.document,
            pack_hash=pack.pack_hash,
            # G3 (Stream B) records the archived source hash. Absent, not a placeholder.
            source_hash=None,
        ),
        applicability=applicability,
        event=dict(facts.get("event") or {}),
        entitlements=_entitlements(cited),
        cause_assessment=_cause_assessment(facts),
        cause_comparison=_comparison(facts=facts, pack=pack),
        excluded_rules=_excluded_rules(pack),
        disclaimer=_disclaimer(pack, cited),
        missing_facts=list(cited.missing_facts or []),
        blocking_reasons=list(cited.blocking_reasons or []),
    )


def _comparison(*, facts: dict[str, Any], pack: Any) -> PolicyCauseComparison:
    """Re-evaluate the same pack with an internal cause substituted. Writes nothing.

    This is what makes the exemption legible: with a beyond-control cause the charter owes nothing,
    and with an internal cause the same rules produce a payable amount. Both figures come from the
    engine; the only thing that changed is an input.
    """
    from app.policy.entitlements import calculate

    if not facts:
        return PolicyCauseComparison(enabled=False, description=COMPARISON_DESCRIPTION)

    altered = {
        **facts,
        "event": {**(facts.get("event") or {}), "type": ALTERNATIVE_EVENT},
        "cause_evidence": {
            "operational_cause": ALTERNATIVE_CAUSE,
            "clearly_attributable": True,
            "external_to_carrier": False,
            "unavoidable_despite_reasonable_measures": False,
        },
    }
    try:
        alternative = calculate(facts=altered, pack=pack)
    except Exception as exc:
        # A comparison is an explanation, not a decision. Losing it must not fail the response.
        log.error(
            "policy_cause_comparison_failed",
            outcome="error",
            detail=type(exc).__name__,
            reason=str(exc)[:200],
        )
        return PolicyCauseComparison(enabled=False, description=COMPARISON_DESCRIPTION)

    return PolicyCauseComparison(
        enabled=True,
        description=COMPARISON_DESCRIPTION,
        alternative=PolicyCauseAlternative(
            event_type=ALTERNATIVE_EVENT,
            operational_cause=ALTERNATIVE_CAUSE,
            external_to_carrier=False,
            outcome=(
                "undetermined"
                if alternative.missing_facts or alternative.blocking_reasons
                else alternative.outcome
            ),
            cash_inr=alternative.cash_inr,
            formula_used=alternative.formula_used,
            rules_fired=list(alternative.rules_fired or []),
            source_clause_refs=list(alternative.source_clause_refs or []),
            missing_facts=list(alternative.missing_facts or []),
            note=(
                "Internal cause, so no beyond-control exemption applies. Same pack, same rules, "
                "one substituted input."
                + (
                    " The cash figure is undetermined because a formula input is not recorded: "
                    f"{', '.join(alternative.missing_facts)}."
                    if alternative.missing_facts
                    else ""
                )
            ),
        ),
    )
