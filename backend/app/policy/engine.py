"""Jurisdiction-neutral rules engine — STREAM B.

The engine knows operators (comparisons, date windows, capped formulas, evidence presence).
It does NOT know the word "DGCA". Jurisdiction-specific content lives entirely in packs.

Specification: policy_packs/in-moca-charter-2019/2019.02/rules.yaml (40 rules) and
test_cases.yaml (23 cases). Those cases are the definition of done.

Non-negotiable behaviours:
  * A rule marked excluded_from_evaluation NEVER evaluates. Surface a supersession notice.
  * A missing required fact yields needs_human, never a guessed amount.
  * An exemption requires its evidence facts. A weather trigger alone never exempts.
  * Every result carries pack id, version, hash, rule id and source clause refs.

## Tri-state conditions

Every condition evaluates to True, False or UNKNOWN, and the difference between the last two
is the whole point. `all` is False as soon as one conjunct is definitively False, even when
others are unknown — that is what stops a cancellation rule from blocking a delay evaluation.
`all` is UNKNOWN when nothing is False but something is unknown.

## When an undetermined rule blocks

A rule that cannot be decided does not silently disappear. It blocks when:

  1. it declares `requires_facts` with `on_missing_required_fact: needs_human` and any of
     those facts is absent — this is the exemption gate;
  2. it would state a cash amount, and no cash rule was decided either way, so reporting zero
     would assert something we have not established;
  3. it fired but its formula inputs are absent.

Otherwise it is recorded in `undetermined_rules` and does not fire. An undetermined rule never
overrides a rule that was decided: if one cash rule is definitively satisfied, its figure
stands and the undetermined one is reported alongside it.
"""

from __future__ import annotations

from datetime import time
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from app.assurance.checks import dedupe
from app.models.enums import ApplicabilityStatus

ENGINE_VERSION: Final = "policy-engine-v1"

# --------------------------------------------------------------------------- blocking codes

REASON_MISSING_REQUIRED_FACT: Final = "MISSING_REQUIRED_FACT"
REASON_UNKNOWN_RULE_OPERATOR: Final = "UNKNOWN_RULE_OPERATOR"
REASON_CONFLICTING_ENTITLEMENTS: Final = "CONFLICTING_ENTITLEMENTS"
REASON_DEFERS_TO_OTHER_JURISDICTION: Final = "DEFERS_TO_OTHER_JURISDICTION"

#: Surfaced instead of evaluating a rule whose source is suspected superseded.
NOTICE_SUPERSESSION_SUSPECTED: Final = "SUPERSESSION_SUSPECTED"

#: Recorded when no cash rule matched at all, so the zero is a stated outcome rather than a
#: default that nobody chose.
CODE_NO_CASH_RULE_MATCHED: Final = "NO_CASH_ENTITLEMENT_RULE_MATCHED"

OUTCOME_EVALUATED: Final = "evaluated"
OUTCOME_NEEDS_HUMAN: Final = "needs_human"
OUTCOME_SUPPRESSED: Final = "suppressed"


class _Unknown:
    """Third truth value.

    `__bool__` raises: an unknown condition must never be silently coerced to false by a
    plain `if`, because that is precisely the bug this type exists to prevent.
    """

    def __bool__(self) -> bool:
        raise TypeError("UNKNOWN is not a boolean; compare with `is UNKNOWN` explicitly")

    def __repr__(self) -> str:
        return "UNKNOWN"


UNKNOWN: Final = _Unknown()

TriState = bool | _Unknown


class _UnknownOperatorError(Exception):
    """Internal control flow: a rule used an operator the DSL does not implement."""

    def __init__(self, operator: str) -> None:
        super().__init__(operator)
        self.operator = operator


# ------------------------------------------------------------------------------- fact access

_MISSING: Final = object()


def _lookup(facts: Any, path: str) -> Any:
    current: Any = facts
    for segment in path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return _MISSING
        current = current[segment]
    return current


def _is_absent(value: Any) -> bool:
    """A fact present but None is absent. A null is not a legal answer."""
    return value is _MISSING or value is None


def absent_facts(facts: dict[str, Any], paths: list[str]) -> list[str]:
    """Which of `paths` are absent from `facts`, in declaration order."""
    return dedupe([path for path in paths if _is_absent(_lookup(facts, path))])


def present_facts(facts: dict[str, Any], paths: list[str]) -> list[str]:
    """Which of `paths` are present, in declaration order."""
    return dedupe([path for path in paths if not _is_absent(_lookup(facts, path))])


def condition_fact_paths(node: Any) -> list[str]:
    """Every fact path a condition references, in declaration order.

    This is what makes the gate's required facts derivable rather than guessed: the facts a
    rule needs are the facts its own `when` clause reads. Nothing here interprets meaning, so
    a new rule contributes its facts without any code change.
    """
    if node is None:
        return []
    if isinstance(node, list):
        return dedupe([path for child in node for path in condition_fact_paths(child)])
    if not isinstance(node, dict):
        return []

    if "fact" in node:
        return [str(node["fact"])]

    paths: list[str] = []
    for key in ("all", "all_of", "any_of", "any"):
        if key in node:
            paths.extend(condition_fact_paths(node[key]))
    if "not" in node:
        paths.extend(condition_fact_paths(node["not"]))
    return dedupe(paths)


# --------------------------------------------------------------------------------- operators


def _parse_local_time(value: Any) -> time | None:
    """Accept a time, "HH:MM[:SS]", or an ISO timestamp whose time part we can read."""
    if isinstance(value, time):
        return value
    if not isinstance(value, str):
        return None

    candidate = value.strip()
    if "T" in candidate:
        candidate = candidate.split("T", 1)[1]
    candidate = candidate.split("+", 1)[0].split("Z", 1)[0]

    parts = candidate.split(":")
    if len(parts) < 2:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
        second = int(float(parts[2])) if len(parts) > 2 and parts[2] else 0
    except ValueError:
        return None
    if not (0 <= hour < 24 and 0 <= minute < 60 and 0 <= second < 60):
        return None
    return time(hour, minute, second)


def _within_local_window(actual: Any, window: Any) -> TriState:
    """True when a local clock time falls inside a window that may wrap past midnight."""
    if not isinstance(window, dict):
        raise _UnknownOperatorError("within_local_window(malformed window)")

    moment = _parse_local_time(actual)
    start = _parse_local_time(window.get("from"))
    end = _parse_local_time(window.get("to"))
    if moment is None or start is None or end is None:
        return UNKNOWN

    if start <= end:
        return start <= moment <= end
    # Wraps midnight, e.g. 20:00 to 03:00.
    return moment >= start or moment <= end


def _apply_operator(operator: str, actual: Any, expected: Any) -> TriState:
    if operator == "within_local_window":
        return _within_local_window(actual, expected)

    try:
        if operator == "present":
            return True
        if operator == "absent":
            return False
        if operator == "confirmed":
            # Human/project confirmation is tri-state: only literal True confirms. False means
            # "not confirmed", not a substantive decision that the guarded rule is inapplicable.
            return True if actual is True else UNKNOWN
        if operator == "eq":
            return actual == expected
        if operator == "ne":
            return actual != expected
        if operator == "lt":
            return actual < expected
        if operator == "lte":
            return actual <= expected
        if operator == "gt":
            return actual > expected
        if operator == "gte":
            return actual >= expected
        if operator == "in":
            return actual in expected
        if operator == "not_in":
            return actual not in expected
    except TypeError:
        # Values that cannot be compared are not a match we can assert either way.
        return UNKNOWN

    raise _UnknownOperatorError(operator)


def _resolve_expected(leaf: dict[str, Any], pack: Any) -> Any:
    """Read `value`, or `value_from` as a dotted path into the pack manifest."""
    if "value_from" in leaf:
        path = str(leaf["value_from"])
        source = getattr(pack, "parameters", None) if pack is not None else None
        if source is None and pack is not None:
            source = getattr(pack, "manifest", None)
        value = _lookup(source or {}, path.removeprefix("pack."))
        if value is _MISSING:
            raise _UnknownOperatorError(f"value_from({path})")
        return value
    return leaf.get("value")


def evaluate_condition(
    node: Any, facts: dict[str, Any], pack: Any = None
) -> tuple[TriState, list[str], dict[str, Any]]:
    """Evaluate a condition node in tri-state logic.

    Returns `(value, unknown_fact_paths, basis)` where `basis` records the facts that made a
    True result true, so a decision can explain itself without re-deriving anything.
    """
    if node is None:
        return True, [], {}

    if isinstance(node, list):
        return evaluate_condition({"all": node}, facts, pack)

    if not isinstance(node, dict):
        raise _UnknownOperatorError(f"malformed condition node: {node!r}")

    # -------------------------------------------------------------------------- conjunction
    if "all" in node or "all_of" in node:
        children = node.get("all") if "all" in node else node.get("all_of")
        unknowns: list[str] = []
        basis: dict[str, Any] = {}
        for child in children or []:
            value, child_unknowns, child_basis = evaluate_condition(child, facts, pack)
            if value is False:
                # Definitively false, so unknown siblings are irrelevant. This is what keeps a
                # cancellation rule from blocking a delay evaluation.
                return False, [], {}
            if value is UNKNOWN:
                unknowns.extend(child_unknowns)
            else:
                basis.update(child_basis)
        if unknowns:
            return UNKNOWN, dedupe(unknowns), {}
        return True, [], basis

    # -------------------------------------------------------------------------- disjunction
    if "any_of" in node or "any" in node:
        children = node.get("any_of") if "any_of" in node else node.get("any")
        unknowns = []
        for child in children or []:
            value, child_unknowns, child_basis = evaluate_condition(child, facts, pack)
            if value is True:
                return True, [], child_basis
            if value is UNKNOWN:
                unknowns.extend(child_unknowns)
        if unknowns:
            return UNKNOWN, dedupe(unknowns), {}
        return False, [], {}

    if "not" in node:
        value, unknowns, _ = evaluate_condition(node["not"], facts, pack)
        if value is UNKNOWN:
            return UNKNOWN, unknowns, {}
        return (not value), [], {}

    # --------------------------------------------------------------------------------- leaf
    if "fact" not in node:
        raise _UnknownOperatorError(f"condition node has no fact: {sorted(node)}")

    path = str(node["fact"])
    operator = str(node.get("op", "eq"))
    actual = _lookup(facts, path)

    if operator == "absent":
        return _is_absent(actual), [], {}

    if _is_absent(actual):
        # Unknown, never false. Treating an absent fact as a failed condition is how a
        # passenger silently loses an entitlement.
        return UNKNOWN, [path], {}

    expected = _resolve_expected(node, pack)
    outcome = _apply_operator(operator, actual, expected)
    if outcome is True:
        return True, [], {path: actual}
    if outcome is UNKNOWN:
        return UNKNOWN, [path], {}
    return False, [], {}


# ---------------------------------------------------------------------------------- formulas

_FARE_BASIC: Final = "fare.one_way_basic_fare_inr"
_FARE_FUEL: Final = "fare.airline_fuel_charge_inr"

#: Formulas the DSL implements, with the facts each one needs. Adding a formula is a reviewed
#: engine change with new tests — see the rule-engine boundary in
#: docs/19-jurisdiction-and-policy-packs.md. It is deliberately not open-ended.
_FORMULA_INPUTS: Final[dict[str, tuple[str, ...]]] = {
    "least_of_cap_and_basic_fare_plus_fuel_charge": (_FARE_BASIC, _FARE_FUEL),
    "basic_fare_plus_fuel_charge": (_FARE_BASIC, _FARE_FUEL),
}


class _CashSpec(BaseModel):
    """How a rule states a cash figure, if it states one at all."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    kind: str  # 'stated' | 'least_of' | 'percentage'
    formula: str
    inputs: tuple[str, ...] = ()
    cap_inr: int | None = None
    amount_inr: int | None = None
    percentage: float | None = None


def _cash_spec(entitlement: dict[str, Any]) -> _CashSpec | None:
    """Identify a rule that states a cash amount.

    A cap or per-kg rate on its own is a liability LIMIT, not a payout, so it is deliberately
    not a cash figure. Returning an amount for it would invent a claim nobody made.
    """
    if entitlement.get("amount_inr") is not None:
        return _CashSpec(
            kind="stated",
            formula=str(entitlement.get("basis") or "stated_amount"),
            amount_inr=int(entitlement["amount_inr"]),
        )

    formula = entitlement.get("formula")
    if formula:
        name = str(formula)
        return _CashSpec(
            kind="least_of",
            formula=name,
            inputs=_FORMULA_INPUTS.get(name, ()),
            cap_inr=entitlement.get("cap_inr"),
        )

    percentage_of = entitlement.get("percentage_of")
    if percentage_of and entitlement.get("percentage") is not None:
        name = str(percentage_of)
        return _CashSpec(
            kind="percentage",
            formula=f"percentage_of_{name}",
            inputs=_FORMULA_INPUTS.get(name, ()),
            cap_inr=entitlement.get("cap_inr"),
            percentage=float(entitlement["percentage"]),
        )

    return None


def states_cash_amount(rule: Any) -> bool:
    """True when this rule would put a figure on the table.

    The distinction the gate depends on: these are the rules whose missing facts make an
    amount unstatable, as opposed to a care rule whose absence merely leaves an entitlement
    unestablished.
    """
    return _cash_spec(rule.entitlement or {}) is not None


def formula_inputs(rule: Any) -> list[str]:
    """The fact paths this rule's formula reads, or [] if it states a fixed amount."""
    spec = _cash_spec(rule.entitlement or {})
    return list(spec.inputs) if spec else []


def is_evidence_gated(rule: Any) -> bool:
    """True when the pack declares required facts AND asks for a human when they are absent.

    That pairing is how a pack marks an exemption as evidence-gated, and it is the only reason
    the engine ever blocks on a rule that would otherwise reduce an entitlement.
    """
    return bool(rule.requires_facts) and rule.on_missing_required_fact == OUTCOME_NEEDS_HUMAN


def is_asserted(rule: Any, facts: dict[str, Any]) -> bool:
    """True when at least one fact the rule declares as required is present.

    An exemption nobody asserted does not apply and must not be demanded — demanding it would
    stall every ordinary case. One asserted fact means the claim is in play, and from then on
    the rest of its evidence is required.
    """
    return bool(present_facts(facts, list(rule.requires_facts)))


def _compute_cash(spec: _CashSpec, facts: dict[str, Any]) -> tuple[int, str]:
    """Return (amount, rendered derivation). Callers must check inputs first."""
    if spec.kind == "stated":
        amount = int(spec.amount_inr or 0)
        return amount, f"stated_amount({amount}) = {amount}"

    basic = _lookup(facts, _FARE_BASIC)
    fuel = _lookup(facts, _FARE_FUEL)
    base = int(basic) + int(fuel)

    if spec.kind == "least_of":
        cap = int(spec.cap_inr) if spec.cap_inr is not None else base
        amount = min(cap, base)
        rendered = f"least_of(cap {cap}, basic_fare {int(basic)} + fuel {int(fuel)}) = {amount}"
        return amount, rendered

    percentage = spec.percentage or 0.0
    raw = round(base * percentage / 100)
    if spec.cap_inr is None:
        return raw, f"{percentage:g}% of (basic_fare {int(basic)} + fuel {int(fuel)}) = {raw}"
    cap = int(spec.cap_inr)
    amount = min(cap, raw)
    rendered = (
        f"least_of(cap {cap}, {percentage:g}% of (basic_fare {int(basic)} + "
        f"fuel {int(fuel)}) = {raw}) = {amount}"
    )
    return amount, rendered


def _reason_code_from_basis(basis: str) -> str:
    """Generic transform, so the engine carries no jurisdiction vocabulary."""
    return basis.upper().removesuffix("_IN_SOURCE")


# ----------------------------------------------------------------------------------- results


class EntitlementResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 'evaluated' | 'needs_human' | 'suppressed'
    outcome: str
    entitlements: list[dict[str, Any]] = Field(default_factory=list)
    cash_inr: int | None = None
    cash_reason_codes: list[str] = Field(default_factory=list)
    rules_fired: list[str] = Field(default_factory=list)
    excluded_rules: list[str] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    missing_facts: list[str] = Field(default_factory=list)

    pack_id: str | None = None
    pack_version: str | None = None
    pack_hash: str | None = None
    pack_status: str | None = None
    verified_mode_eligible: bool = False
    source_document_verified: bool = False
    source_clause_refs: list[str] = Field(default_factory=list)
    # Human-readable derivation, e.g. "least_of(cap 7500, 4200 + 800) = 5000"
    formula_used: str | None = None

    # ---- added by Stream B alongside the Wave 0 skeleton fields ----
    #: The named formula the pack selected, distinct from its rendered derivation above.
    formula: str | None = None
    #: Rules that could not be decided, with the facts that were absent. Never silently
    #: dropped: an operator needs to see what the engine could not answer.
    undetermined_rules: list[dict[str, Any]] = Field(default_factory=list)
    #: Supersession and similar notices surfaced instead of evaluating a rule.
    notices: list[dict[str, Any]] = Field(default_factory=list)
    #: Prohibitions and duties a fired rule imposes that are not themselves entitlements,
    #: such as "a credit shell must not be the default". Kept separate so they cannot be
    #: mistaken for something owed to the passenger.
    obligations: list[dict[str, Any]] = Field(default_factory=list)
    pack_ui_label: str | None = None
    currency: str | None = None
    engine_version: str = ENGINE_VERSION

    @property
    def requires_human(self) -> bool:
        return self.outcome == OUTCOME_NEEDS_HUMAN

    @property
    def entitlement_types(self) -> list[str]:
        return dedupe([str(item.get("type", "")) for item in self.entitlements])

    @property
    def may_be_presented_as_current_law(self) -> bool:
        """Project approval alone is never regulatory/current-law standing."""
        return (
            self.pack_status == "approved"
            and self.verified_mode_eligible
            and self.source_document_verified
        )


class ApplicabilityResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ApplicabilityStatus
    pack_id: str
    pack_version: str
    basis: dict[str, Any] = Field(default_factory=dict)
    required_facts: list[str] = Field(default_factory=list)
    missing_facts: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


# ------------------------------------------------------------------------------- evaluation


def _pack_identity(pack: Any) -> dict[str, Any]:
    status = getattr(pack, "status", None)
    return {
        "pack_id": getattr(pack, "pack_id", None),
        "pack_version": getattr(pack, "version", None),
        "pack_hash": getattr(pack, "pack_hash", None),
        "pack_status": getattr(status, "value", status),
        "verified_mode_eligible": bool(getattr(pack, "verified_mode_eligible", False)),
        "source_document_verified": bool(getattr(pack, "source_document_verified", False)),
        "pack_ui_label": getattr(pack, "ui_label", None),
        "currency": getattr(pack, "currency", None),
    }


def _notices_for(pack: Any) -> list[dict[str, Any]]:
    """Excluded rules produce a notice instead of an evaluation."""
    notices: list[dict[str, Any]] = []
    for rule in getattr(pack, "excluded_rules", []):
        notices.append(
            {
                "rule_id": rule.id,
                "notice": NOTICE_SUPERSESSION_SUSPECTED,
                "status": rule.status,
                "note": rule.supersession_note,
                "evaluated": False,
            }
        )
    return notices


def _blocked(
    *,
    pack: Any,
    reasons: list[str],
    missing: list[str],
    excluded: list[str],
    notices: list[dict[str, Any]],
    undetermined: list[dict[str, Any]],
) -> EntitlementResult:
    """needs_human, with no figure attached.

    `cash_inr` stays None deliberately. A blocked evaluation that reported zero would look
    exactly like a decision that nothing is owed.
    """
    return EntitlementResult(
        outcome=OUTCOME_NEEDS_HUMAN,
        cash_inr=None,
        blocking_reasons=dedupe(reasons),
        missing_facts=dedupe(missing),
        excluded_rules=excluded,
        notices=notices,
        undetermined_rules=undetermined,
        **_pack_identity(pack),
    )


def evaluate(*, facts: dict[str, Any], pack: Any) -> EntitlementResult:
    """Evaluate a reviewed pack's rules against trip facts."""
    excluded = [rule.id for rule in getattr(pack, "excluded_rules", [])]
    notices = _notices_for(pack)

    fired: list[Any] = []
    undetermined: list[dict[str, Any]] = []

    for rule in pack.evaluable_rules:
        try:
            value, unknown_paths, _ = evaluate_condition(rule.when, facts, pack)
        except _UnknownOperatorError as exc:
            # Aggregation rule 1: an operator we do not implement is a hard failure, never a
            # rule that quietly does not apply.
            return _blocked(
                pack=pack,
                reasons=[REASON_UNKNOWN_RULE_OPERATOR],
                missing=[],
                excluded=excluded,
                notices=[
                    *notices,
                    {
                        "rule_id": rule.id,
                        "notice": REASON_UNKNOWN_RULE_OPERATOR,
                        "operator": exc.operator,
                    },
                ],
                undetermined=undetermined,
            )

        if value is True:
            fired.append(rule)
        elif value is UNKNOWN:
            undetermined.append({"rule_id": rule.id, "missing_facts": unknown_paths})

    undetermined_ids = {entry["rule_id"] for entry in undetermined}

    # ---------------------------------------------------------- gate 1: declared evidence
    #
    # An exemption is the carrier's defence, so it has to prove itself. The distinction that
    # matters is between an exemption that was never asserted and one that was asserted
    # incompletely:
    #
    #   * condition definitively False -> refused on the facts. Nothing to resolve.
    #   * no declared fact present at all -> never asserted. It does not apply, and the
    #     passenger's entitlement stands. Blocking here would stall every ordinary case.
    #   * some declared facts present and some absent -> PARTIALLY EVIDENCED. Block. Somebody
    #     is claiming the exemption and the decisive fact is missing, which is exactly the
    #     weather-without-reasonable-measures case. Inferring either way would be a legal
    #     judgement made by omission.
    #
    # This gate runs even when another rule has already produced a figure, because an
    # exemption could have suppressed that figure.
    for rule in [*fired, *(r for r in pack.evaluable_rules if r.id in undetermined_ids)]:
        if not rule.requires_facts or rule.on_missing_required_fact != OUTCOME_NEEDS_HUMAN:
            continue
        absent = absent_facts(facts, rule.requires_facts)
        condition_gaps = next(
            (e["missing_facts"] for e in undetermined if e["rule_id"] == rule.id), []
        )
        unconfirmed = [
            path for path in condition_gaps if path in rule.requires_facts and path not in absent
        ]
        if not absent and not unconfirmed:
            continue
        asserted = [path for path in rule.requires_facts if path not in absent]
        if not asserted:
            continue
        return _blocked(
            pack=pack,
            reasons=[REASON_MISSING_REQUIRED_FACT],
            # Condition gaps first: that is the fact the rule actually stalled on.
            missing=[*condition_gaps, *absent],
            excluded=excluded,
            notices=[
                *notices,
                {
                    "rule_id": rule.id,
                    "notice": REASON_MISSING_REQUIRED_FACT,
                    "asserted_facts": asserted,
                    "absent_facts": absent,
                },
            ],
            undetermined=undetermined,
        )

    # ------------------------------------------------------- gate 2: deferred jurisdiction
    for rule in fired:
        deferral = (rule.effect or {}).get("defers_to_jurisdiction")
        if deferral:
            return _blocked(
                pack=pack,
                reasons=[REASON_DEFERS_TO_OTHER_JURISDICTION],
                missing=[],
                excluded=excluded,
                notices=[
                    *notices,
                    {
                        "rule_id": rule.id,
                        "notice": REASON_DEFERS_TO_OTHER_JURISDICTION,
                        "defers_to": deferral,
                    },
                ],
                undetermined=undetermined,
            )

    # ----------------------------------------------------------------- cash determination
    decided: list[tuple[Any, _CashSpec]] = []
    for rule in fired:
        spec = _cash_spec(rule.entitlement or {})
        if spec is not None:
            decided.append((rule, spec))

    undecided_cash: list[dict[str, Any]] = []
    for entry in undetermined:
        rule = pack.rule(entry["rule_id"])
        if rule is not None and _cash_spec(rule.entitlement or {}) is not None:
            undecided_cash.append(entry)

    # gate 3: nothing decided, but something might have applied. Reporting zero here would
    # assert that no cash is owed, which is not what we know.
    if not decided and undecided_cash:
        return _blocked(
            pack=pack,
            reasons=[REASON_MISSING_REQUIRED_FACT],
            missing=[path for entry in undecided_cash for path in entry["missing_facts"]],
            excluded=excluded,
            notices=notices,
            undetermined=undetermined,
        )

    # gate 4: a rule fired but its inputs are absent, so its figure cannot be computed.
    missing_inputs: list[str] = []
    for _rule, spec in decided:
        missing_inputs.extend(absent_facts(facts, list(spec.inputs)))
    if missing_inputs:
        return _blocked(
            pack=pack,
            reasons=[REASON_MISSING_REQUIRED_FACT],
            missing=missing_inputs,
            excluded=excluded,
            notices=notices,
            undetermined=undetermined,
        )

    computed: list[tuple[Any, _CashSpec, int, str]] = []
    for rule, spec in decided:
        amount, rendered = _compute_cash(spec, facts)
        computed.append((rule, spec, amount, rendered))

    # gate 5: two different figures with no reviewed precedence rule.
    distinct = {amount for _rule, _spec, amount, _rendered in computed}
    if len(distinct) > 1 and not getattr(pack, "conflict_rules_defined", False):
        return _blocked(
            pack=pack,
            reasons=[REASON_CONFLICTING_ENTITLEMENTS],
            missing=[],
            excluded=excluded,
            notices=[
                *notices,
                {
                    "notice": REASON_CONFLICTING_ENTITLEMENTS,
                    "rule_ids": [rule.id for rule, _s, _a, _r in computed],
                    "amounts": sorted(distinct),
                },
            ],
            undetermined=undetermined,
        )

    # ------------------------------------------------------------------- build entitlements
    entitlements: list[dict[str, Any]] = []
    cash_reason_codes: list[str] = []
    formula_name: str | None = None
    formula_rendered: str | None = None
    cash_inr: int | None = None

    for rule in fired:
        entitlement = dict(rule.entitlement or {})
        if not entitlement:
            continue

        record: dict[str, Any] = {
            **entitlement,
            "type": entitlement.get("type"),
            "rule_id": rule.id,
            "source_clause_refs": list(rule.source_clause_refs),
            "interpretation": rule.interpretation,
        }

        match = next((item for item in computed if item[0] is rule), None)
        if match is not None:
            _rule, spec, amount, rendered = match
            record["amount_inr"] = amount
            record["formula"] = spec.formula
            record["formula_used"] = rendered
            record["outcome"] = "owed" if amount > 0 else "not_owed"
            cash_inr = amount if cash_inr is None else cash_inr
            formula_name = formula_name or spec.formula
            formula_rendered = formula_rendered or rendered
            basis = entitlement.get("basis")
            if basis:
                cash_reason_codes.append(_reason_code_from_basis(str(basis)))
        elif (
            entitlement.get("cap_inr")
            or entitlement.get("cap_sdr")
            or entitlement.get("rate_inr_per_kg")
            or entitlement.get("rate_sdr_per_kg")
        ):
            # A liability ceiling, not a payment.
            record["outcome"] = "limit"
        else:
            record["outcome"] = "owed"

        entitlements.append(record)

    if cash_inr is None:
        cash_inr = 0
        cash_reason_codes.append(CODE_NO_CASH_RULE_MATCHED)

    # ---------------------------------------------------------------------- apply effects
    suppressed_types: set[str] = set()
    obligations: list[dict[str, Any]] = []

    for rule in fired:
        effect = rule.effect or {}
        if not effect:
            continue
        for entitlement_type in effect.get("suppresses_entitlement_types") or []:
            suppressed_types.add(str(entitlement_type))
        code = effect.get("reason_code")
        if code:
            cash_reason_codes.append(str(code))
        if effect.get("forbids_default"):
            obligations.append(
                {
                    "rule_id": rule.id,
                    "forbids_default": effect["forbids_default"],
                    "source_clause_refs": list(rule.source_clause_refs),
                }
            )

    if suppressed_types:
        entitlements = [
            item for item in entitlements if str(item.get("type")) not in suppressed_types
        ]
        if "cash" in suppressed_types:
            cash_inr = 0
            formula_name = None
            formula_rendered = None

    outcome = OUTCOME_SUPPRESSED if suppressed_types else OUTCOME_EVALUATED

    return EntitlementResult(
        outcome=outcome,
        entitlements=entitlements,
        obligations=obligations,
        cash_inr=cash_inr,
        cash_reason_codes=dedupe(cash_reason_codes),
        rules_fired=[rule.id for rule in fired],
        excluded_rules=excluded,
        notices=notices,
        undetermined_rules=undetermined,
        source_clause_refs=dedupe([ref for rule in fired for ref in rule.source_clause_refs]),
        formula=formula_name,
        formula_used=formula_rendered,
        **_pack_identity(pack),
    )
