"""What the gate must demand, derived from the pack — STREAM B.

The problem this solves: `evidence_complete` and `policy_compliant` are only as strong as the
`required_facts` and `constraints` they are handed. Given empty lists they pass, and the gate
reports two green checks that verified nothing. A caller cannot be asked to fill them in,
because the facts a legal rule needs are a property of the rule, not of the orchestrator.

So Stream B derives them. `gate_requirements` identifies the canonical pack the resolver
selected and the canonical rules that decide the outcome, then reports exactly which facts
those rules read and which constraints the resulting payload must satisfy.

## What counts as required

Three sources, all grounded in pack data rather than in judgement here:

1. **Applicability facts** — `required_facts` in applicability.yaml. Always demanded; without
   them the resolver cannot even say whether the pack governs the trip.
2. **Facts the deciding rules read** — for every candidate rule that would state a cash
   amount, the fact paths in its own `when` clause plus its formula inputs. A new rule
   contributes its facts with no code change here.
3. **Evidence for an exemption that has been asserted** — the `requires_facts` of an
   evidence-gated rule, but only once at least one of them is present.

That third one carries the weight. Demanding exemption evidence unconditionally would block
every ordinary cancellation, because no cause evidence is present in the common case and
absence would read as an unanswered question. Demanding it never would let a half-made force
majeure claim through. So it is demanded exactly when the claim is in play, which mirrors what
the rules engine already does when it decides whether to block.

## What is deliberately not required

Care entitlements — meals, hotel, ground transfer. If block time is missing, a delay's meals
provision is *unestablished*, not *wrong*, and the engine reports it in `undetermined_rules`
for an operator to see. Demanding those facts would block every incompletely-recorded delay
and teach people to bypass the gate. The line drawn here is that the gate demands the facts
that determine a **figure**, because that is what a passenger relies on and what the gate
authorises.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from app.assurance.checks import dedupe
from app.assurance.gate import POLICY_BEARING_ACTIONS
from app.config import PolicyMode, Settings, get_settings, resolve_repo_path
from app.errors import PackNotVerifiedEligible, PolicyPackUnavailable
from app.models.enums import ApplicabilityStatus
from app.policy.business_constraints import (
    business_constraint_versions,
    constraints_from_rows,
    load_mappings,
)
from app.policy.engine import (
    UNKNOWN,
    condition_fact_paths,
    evaluate_condition,
    formula_inputs,
    is_asserted,
    is_evidence_gated,
    states_cash_amount,
)
from app.policy.entitlements import CitedEntitlement, calculate
from app.policy.loader import LoadedPack, load_pack

#: Why a fact is demanded. Recorded per fact so nobody has to reverse-engineer the reason.
ORIGIN_APPLICABILITY: Final = "applicability"
ORIGIN_RULE_CONDITION: Final = "rule_condition"
ORIGIN_FORMULA_INPUT: Final = "formula_input"
ORIGIN_EXEMPTION_EVIDENCE: Final = "exemption_evidence"

#: Constraint ids, so a breach in an audit record is traceable to the rule that imposed it.
CONSTRAINT_CASH_MATCHES_ENGINE: Final = "policy.cash_matches_engine"
CONSTRAINT_CURRENCY: Final = "policy.currency_matches_pack"
CONSTRAINT_PACK_VERSION: Final = "policy.pack_version_matches"
CONSTRAINT_NO_EXCLUDED_RULES: Final = "policy.no_excluded_rule_cited"
CONSTRAINT_NOT_CURRENT_LAW: Final = "policy.not_presented_as_current_law"
CONSTRAINT_PACK_UNAVAILABLE: Final = "policy.pack_unavailable"
CONSTRAINT_EVALUATION_BLOCKED: Final = "policy.evaluation_blocked"


class FactRequirement(BaseModel):
    """One demanded fact and the rules that demand it."""

    model_config = ConfigDict(extra="forbid")

    path: str
    origin: str
    demanded_by: list[str] = Field(default_factory=list)


class GateRequirements(BaseModel):
    """Everything the gate needs in order to check something real.

    Feed `required_facts` and `constraints` straight into `GateInputs`. `requirements` and
    `selected_rule_ids` are provenance: they answer "why is this fact required" without
    anybody guessing.
    """

    model_config = ConfigDict(extra="forbid")

    action_type: str
    #: False when the pack has nothing to say about this action, e.g. reserving a hotel block.
    policy_bearing: bool

    required_facts: list[str] = Field(default_factory=list)
    constraints: list[dict[str, Any]] = Field(default_factory=list)
    requirements: list[FactRequirement] = Field(default_factory=list)

    #: The canonical rules that decide this outcome, in pack order.
    selected_rule_ids: list[str] = Field(default_factory=list)
    excluded_rule_ids: list[str] = Field(default_factory=list)

    pack_id: str | None = None
    pack_version: str | None = None
    pack_hash: str | None = None
    pack_status: str | None = None
    policy_mode: str | None = None
    applicability_status: str | None = None
    resolver_version: str | None = None

    #: Present when requirements could not be derived. The constraints already fail closed;
    #: this names the reason for the audit record.
    blocking_reasons: list[str] = Field(default_factory=list)

    #: service.constraint_key -> version, for every commercial limit this evaluation was decided
    #: against. Recorded for the same reason as the pack version: a replay must know which
    #: numbers applied.
    business_constraint_versions: dict[str, str] = Field(default_factory=dict)

    @property
    def is_vacuous(self) -> bool:
        """True when these requirements would let both policy checks pass without verifying.

        A policy-bearing action whose requirements are vacuous is a defect, and the gate
        refuses rather than reporting two green checks.
        """
        return not self.required_facts and not self.constraints


def _blocking(
    *, action_type: str, reason_id: str, reason: str, code: str, pack: LoadedPack | None = None
) -> GateRequirements:
    """Requirements that cannot be satisfied, so the gate blocks through its normal path."""
    constraint: dict[str, Any] = {"id": reason_id, "reason": reason}
    if code == "POLICY_PACK_UNAVAILABLE":
        constraint["pack_unavailable"] = True
    else:
        constraint["unsatisfiable"] = True

    return GateRequirements(
        action_type=action_type,
        policy_bearing=True,
        constraints=[constraint],
        blocking_reasons=[code],
        pack_id=pack.pack_id if pack else None,
        pack_version=pack.version if pack else None,
        pack_hash=pack.pack_hash if pack else None,
        pack_status=pack.status.value if pack else None,
    )


def _candidate_rules(*, facts: dict[str, Any], pack: LoadedPack) -> list[Any]:
    """Rules whose condition is not definitively false on the facts we have.

    A rule ruled out by a fact we actually know is not a candidate — that is what keeps a
    cancellation rule from demanding facts during a delay. A rule that cannot be decided IS a
    candidate, because it might yet apply.
    """
    candidates: list[Any] = []
    for rule in pack.evaluable_rules:
        try:
            value, _, _ = evaluate_condition(rule.when, facts, pack)
        except Exception:
            # A malformed rule must not silently drop out of the candidate set. Keeping it makes
            # its facts required, and the engine will refuse on the same rule.
            candidates.append(rule)
            continue
        if value is True or value is UNKNOWN:
            candidates.append(rule)
    return candidates


def _derive_fact_requirements(
    *, facts: dict[str, Any], pack: LoadedPack, candidates: list[Any]
) -> tuple[list[FactRequirement], list[str]]:
    """Build the demanded-fact list with its provenance, plus the deciding rule ids."""
    demanded: dict[str, FactRequirement] = {}

    def demand(path: str, origin: str, rule_id: str | None) -> None:
        existing = demanded.get(path)
        if existing is None:
            demanded[path] = FactRequirement(
                path=path, origin=origin, demanded_by=[rule_id] if rule_id else []
            )
        elif rule_id and rule_id not in existing.demanded_by:
            existing.demanded_by.append(rule_id)

    # 1. Applicability. Without these the resolver cannot decide whether the pack governs.
    for path in pack.required_facts:
        demand(path, ORIGIN_APPLICABILITY, None)

    selected: list[str] = []

    for rule in candidates:
        # 2. Rules that would state a figure.
        if states_cash_amount(rule):
            selected.append(rule.id)
            for path in condition_fact_paths(rule.when):
                demand(path, ORIGIN_RULE_CONDITION, rule.id)
            for path in formula_inputs(rule):
                demand(path, ORIGIN_FORMULA_INPUT, rule.id)

        # 3. Evidence for an exemption, once it has been asserted.
        if is_evidence_gated(rule) and is_asserted(rule, facts):
            selected.append(rule.id)
            for path in condition_fact_paths(rule.when):
                demand(path, ORIGIN_RULE_CONDITION, rule.id)
            for path in rule.requires_facts:
                demand(path, ORIGIN_EXEMPTION_EVIDENCE, rule.id)

        # An effect rule with no declared evidence still decides whether cash survives, so the
        # facts it reads are required. `defers_to_jurisdiction` is the important one: whether
        # the carrier is foreign decides whether this pack governs refunds at all.
        elif rule.effect and not is_evidence_gated(rule):
            selected.append(rule.id)
            for path in condition_fact_paths(rule.when):
                demand(path, ORIGIN_RULE_CONDITION, rule.id)

    return list(demanded.values()), dedupe(selected)


def _derive_constraints(*, pack: LoadedPack, cited: CitedEntitlement) -> list[dict[str, Any]]:
    """Assertions the proposed payload must satisfy to be consistent with the law.

    These are what stop a figure changing between the engine computing it and the action
    executing. Every one names a payload field, so the contract for a caller is explicit.
    """
    constraints: list[dict[str, Any]] = [
        {
            "id": CONSTRAINT_PACK_VERSION,
            "field": "pack_version",
            "op": "eq",
            "value": pack.version,
        },
        {
            "id": CONSTRAINT_NO_EXCLUDED_RULES,
            "field": "cited_rule_ids",
            "op": "disjoint_from",
            "value": [rule.id for rule in pack.excluded_rules],
        },
    ]

    if not pack.may_be_called_current_law:
        constraints.append(
            {
                "id": CONSTRAINT_NOT_CURRENT_LAW,
                "field": "presented_as_current_law",
                "op": "eq",
                "value": False,
            }
        )

    # Only assert a figure when the engine actually produced one. A blocked evaluation has no
    # amount to compare against, and its missing facts are already demanded above.
    if cited.cash_inr is not None:
        constraints.append(
            {
                "id": CONSTRAINT_CASH_MATCHES_ENGINE,
                "field": "cash_inr",
                "op": "eq",
                "value": cited.cash_inr,
            }
        )
        if cited.currency:
            constraints.append(
                {
                    "id": CONSTRAINT_CURRENCY,
                    "field": "currency",
                    "op": "eq",
                    "value": cited.currency,
                }
            )

    return constraints


def _business_constraints(
    *, action_type: str, business_rows: list[dict[str, Any]] | None
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Commercial limits for this action, translated from Stream C's stored rows.

    Returns ([], {}) when the caller supplied no rows, so a caller that has not wired them yet
    behaves exactly as before. A malformed mapping file is a blocking constraint rather than an
    empty list: a limit nobody can read must not read as no limit.
    """
    if not business_rows:
        return [], {}

    try:
        mappings = load_mappings()
    except PolicyPackUnavailable as exc:
        return (
            [
                {
                    "id": "business.mappings_unavailable",
                    "unsatisfiable": True,
                    "reason": exc.message,
                }
            ],
            {},
        )

    constraints = constraints_from_rows(
        action_type=action_type, rows=business_rows, mappings=mappings
    )

    # Record a version only for a limit that actually applied. A row that produced no constraint
    # was not decided against, and listing it would pad the audit record with numbers that had no
    # bearing on the outcome.
    applied = {str(constraint["id"]) for constraint in constraints if constraint.get("id")}
    versions = {
        key: version
        for key, version in business_constraint_versions(business_rows).items()
        if f"business.{key}" in applied
    }
    return constraints, versions


def gate_requirements(
    *,
    action_type: str,
    facts: dict[str, Any],
    pack: LoadedPack | None = None,
    settings: Settings | None = None,
    business_rows: list[dict[str, Any]] | None = None,
) -> GateRequirements:
    """Derive what the gate must demand for this action, from the pack in force.

    Feed the result into `GateInputs(required_facts=..., constraints=...)`. Callers must not
    assemble either list themselves: the facts a rule needs are a property of the rule.

    `business_rows` is Stream C's `business_constraint` query result, as returned by
    `app.db.scenario_queries.load_business_constraints`. Supplying it lets the gate refuse a
    proposed action that breaches a commercial limit BEFORE it runs, instead of the service
    refusing internally where the decision is not recorded as an authorisation. Omitting it
    changes nothing.

    Never raises. A pack that cannot be loaded, applicability that cannot be resolved and an
    entitlement that cannot be computed all come back as requirements that fail closed, so
    there is one path through the gate rather than an exception branch around it.
    """
    active = settings or get_settings()
    business, business_versions = _business_constraints(
        action_type=action_type, business_rows=business_rows
    )

    if action_type not in POLICY_BEARING_ACTIONS:
        # The pack has nothing to say about reserving a hotel block or checking connections.
        # Their limits are commercial, and they arrive through `business_rows`.
        return GateRequirements(
            action_type=action_type,
            policy_bearing=False,
            policy_mode=active.policy_mode.value,
            constraints=business,
            business_constraint_versions=business_versions,
        )

    try:
        loaded = pack or load_pack(
            pack_dir=resolve_repo_path(Path(active.policy_pack_dir)),
            pack_id=active.policy_pack_id,
            version=active.policy_pack_version,
            mode=active.policy_mode,
        )
    except PackNotVerifiedEligible as exc:
        return _blocking(
            action_type=action_type,
            reason_id=CONSTRAINT_PACK_UNAVAILABLE,
            reason=exc.message,
            code="PACK_NOT_VERIFIED_ELIGIBLE",
        )
    except PolicyPackUnavailable as exc:
        return _blocking(
            action_type=action_type,
            reason_id=CONSTRAINT_PACK_UNAVAILABLE,
            reason=exc.message,
            code="POLICY_PACK_UNAVAILABLE",
        )

    cited = calculate(facts=facts, pack=loaded, settings=active)

    candidates = _candidate_rules(facts=facts, pack=loaded)
    requirements, selected = _derive_fact_requirements(
        facts=facts, pack=loaded, candidates=candidates
    )
    constraints = _derive_constraints(pack=loaded, cited=cited)

    blocking: list[str] = []
    if cited.requires_human:
        blocking = list(cited.blocking_reasons)
        # A block the demanded facts cannot express — an unresolved conflict between two
        # entitlements, or a deferral to another jurisdiction — needs a constraint of its own.
        # Without this the gate could find every demanded fact present and authorise an action
        # the policy layer had already refused.
        unnamed = not cited.missing_facts or not set(cited.missing_facts) & {
            requirement.path for requirement in requirements
        }
        if unnamed:
            constraints.append(
                {
                    "id": CONSTRAINT_EVALUATION_BLOCKED,
                    "unsatisfiable": True,
                    "reason": (
                        f"policy evaluation returned needs_human: "
                        f"{', '.join(cited.blocking_reasons) or 'no reason recorded'}"
                    ),
                }
            )

    # Anything the engine reported missing is required, whatever else was derived. The engine
    # is the authority on what stopped it.
    known = {requirement.path for requirement in requirements}
    for path in cited.missing_facts:
        if path not in known:
            requirements.append(
                FactRequirement(path=path, origin=ORIGIN_EXEMPTION_EVIDENCE, demanded_by=[])
            )

    applicability = cited.applicability[0]["status"] if cited.applicability else None

    return GateRequirements(
        action_type=action_type,
        policy_bearing=True,
        required_facts=[requirement.path for requirement in requirements],
        # A policy-bearing action can also be subject to a commercial limit, so both sets apply.
        constraints=[*constraints, *business],
        business_constraint_versions=business_versions,
        requirements=requirements,
        selected_rule_ids=selected,
        excluded_rule_ids=[rule.id for rule in loaded.excluded_rules],
        pack_id=loaded.pack_id,
        pack_version=loaded.version,
        pack_hash=loaded.pack_hash,
        pack_status=loaded.status.value,
        policy_mode=active.policy_mode.value,
        applicability_status=applicability or ApplicabilityStatus.undetermined.value,
        resolver_version=cited.resolver_version,
        blocking_reasons=dedupe(blocking),
    )


def unresolved_pack_mode(settings: Settings | None = None) -> PolicyMode:
    """The mode requirements will be derived under. Exposed for logging and the mode endpoint."""
    return (settings or get_settings()).policy_mode
