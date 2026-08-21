"""The six assurance checks — STREAM B.

Pure functions. No I/O, no database, no network: everything a check needs arrives in its
arguments so it is trivially unit-testable and reproducible. `now` is injected rather than
read from the clock for the same reason — a replay of the same inputs must yield the same
result.

Each returns a CheckResult with PASS / WARN / FAIL and a machine-readable reason code. The
UI maps codes to copy; it never parses the free-text `reason`.

Two rules run through all six:

  * A fact present but None counts as ABSENT. That single distinction is what stops a null
    being silently treated as a legal answer.
  * Unknown means dangerous. An unrecognised source kind, task state or operator fails
    closed rather than being skipped.

Only `action_risk` sets `tier`, and only `sources_fresh` can return WARN through config.
Nothing here decides whether execution may proceed — that is `gate.aggregate`.

Stream B's definition of done is the 23 cases in
policy_packs/in-moca-charter-2019/2019.02/test_cases.yaml.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Final

from app.assurance.contract import AssuranceConfig, CheckName, CheckResult, ReasonCode
from app.models.enums import CheckState, RiskTier, TaskState


class _Missing:
    """Sentinel distinguishing 'key absent' from 'key present with value None'.

    Both are treated as absent by `evidence_complete`; the type exists so intermediate
    lookups can report which of the two occurred.
    """

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return "<missing>"


_MISSING: Final = _Missing()


def _lookup(facts: Mapping[str, Any] | Any, path: str) -> Any:
    """Resolve a dotted path, returning `_MISSING` if any segment is absent."""
    current: Any = facts
    for segment in path.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            return _MISSING
        current = current[segment]
    return current


def _is_absent(value: Any) -> bool:
    """True when a fact is missing OR explicitly null.

    An explicit `False` is an answer and must not be confused with absence. `None` is not
    an answer.
    """
    return value is _MISSING or value is None


def dedupe(items: Sequence[str]) -> list[str]:
    """Preserve declaration order while removing repeats.

    Declaration order, not alphabetical: the pack's fail-closed cases name missing facts in
    the order the rule declares them, and the engine and the gate must agree.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _passed(name: CheckName, *, tier: RiskTier | None = None) -> CheckResult:
    return CheckResult(name=name, state=CheckState.passed, reason_code=ReasonCode.OK, tier=tier)


# --------------------------------------------------------------------------- 1. evidence


def evidence_complete(*, required_facts: list[str], provided_facts: dict[str, Any]) -> CheckResult:
    """FAIL when any fact the selected rule requires is absent.

    A fact present but None counts as absent — that distinction is what stops a null from
    being silently treated as a legal answer. An explicit `False` is a real answer and
    passes.

    `required_facts` are dotted paths into the nested fact dictionary, e.g.
    `cause_evidence.unavoidable_despite_reasonable_measures`.

    No required facts is a PASS: whether a rule *should* have declared a fact is the pack's
    responsibility, not this check's.
    """
    missing = dedupe([path for path in required_facts if _is_absent(_lookup(provided_facts, path))])
    if not missing:
        return _passed(CheckName.evidence_complete)

    if len(missing) == 1:
        reason = f"{missing[0]} is absent"
    else:
        reason = f"{len(missing)} required facts are absent: {', '.join(missing)}"

    return CheckResult(
        name=CheckName.evidence_complete,
        state=CheckState.failed,
        reason_code=ReasonCode.MISSING_REQUIRED_FACT,
        reason=reason,
    )


# -------------------------------------------------------------------------- 2. freshness

#: Source kind -> (FreshnessLimits attribute, minutes per configured unit).
#: The kind is the segment before the first colon in a source key, e.g. `metar:VOBL`.
#: This map is closed because FreshnessLimits is closed; an unlisted kind fails closed.
_FRESHNESS_LIMITS: Final[dict[str, tuple[str, int]]] = {
    "metar": ("metar_minutes", 1),
    "taf": ("taf_minutes", 1),
    "flight_status": ("flight_status_minutes", 1),
    "policy_pack": ("policy_pack_days", 1440),
}

# Problem severity. Higher wins, so the reported code names the most serious defect.
_RANK_STALE: Final = 1
_RANK_NO_TIMESTAMP: Final = 2
_RANK_NO_LIMIT_CONFIGURED: Final = 3


def sources_fresh(
    *,
    sources: dict[str, datetime | None],
    now: datetime,
    config: AssuranceConfig,
    action_type: str,
) -> CheckResult:
    """FAIL when a source exceeds its configured max age.

    Source keys are `"<kind>:<identifier>"` — `metar:VOBL`, `flight_status:AI2841`,
    `policy_pack:in-moca-charter-2019`.

    Only genuine staleness is downgradable to WARN, and only when
    `config.warn_permitted(action_type, sources_fresh)` is true. Everything else is FAIL:

      * A source with no timestamp is FAIL, never assumed fresh, and never downgraded — the
        config tolerance covers *known* staleness on a reversible action, and an unknown age
        is not known staleness.
      * A timestamp that is not timezone-aware is FAIL. It is ambiguous by up to fourteen
        hours, which is not a usable measurement.
      * A future-dated timestamp is FAIL. A broken feed must not read as maximally fresh.
      * A source kind with no configured limit is FAIL with CONFIG_MISSING. There is no
        freshness bound to check against, and unknown means dangerous.

    `now` is treated as UTC if naive, because it is our own clock rather than external data.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    # (rank, code, detail, key, downgradable)
    problems: list[tuple[int, ReasonCode, str, str, bool]] = []

    for key, timestamp in sorted(sources.items()):
        kind = key.split(":", 1)[0]
        limit_field = _FRESHNESS_LIMITS.get(kind)

        if limit_field is None:
            problems.append(
                (
                    _RANK_NO_LIMIT_CONFIGURED,
                    ReasonCode.CONFIG_MISSING,
                    f"{key} has no configured freshness limit for source kind '{kind}'",
                    key,
                    False,
                )
            )
            continue

        if timestamp is None:
            problems.append(
                (
                    _RANK_NO_TIMESTAMP,
                    ReasonCode.SOURCE_MISSING_TIMESTAMP,
                    f"{key} has no timestamp",
                    key,
                    False,
                )
            )
            continue

        if timestamp.tzinfo is None:
            problems.append(
                (
                    _RANK_NO_TIMESTAMP,
                    ReasonCode.SOURCE_MISSING_TIMESTAMP,
                    f"{key} timestamp is not timezone-aware",
                    key,
                    False,
                )
            )
            continue

        attribute, minutes_per_unit = limit_field
        limit_minutes = getattr(config.freshness, attribute) * minutes_per_unit
        age_minutes = (now - timestamp).total_seconds() / 60

        if age_minutes < 0:
            problems.append(
                (
                    _RANK_STALE,
                    ReasonCode.SOURCE_STALE,
                    f"{key} is dated {int(-age_minutes)}m in the future",
                    key,
                    False,
                )
            )
        elif age_minutes > limit_minutes:
            problems.append(
                (
                    _RANK_STALE,
                    ReasonCode.SOURCE_STALE,
                    f"{key} {int(age_minutes)}m old, max {limit_minutes}m",
                    key,
                    True,
                )
            )

    if not problems:
        return _passed(CheckName.sources_fresh)

    worst_rank = max(rank for rank, *_ in problems)
    selected = [problem for problem in problems if problem[0] == worst_rank]

    downgradable = all(problem[4] for problem in selected)
    permitted = config.warn_permitted(action_type, CheckName.sources_fresh)
    state = CheckState.warn if downgradable and permitted else CheckState.failed

    return CheckResult(
        name=CheckName.sources_fresh,
        state=state,
        reason_code=selected[0][1],
        reason="; ".join(problem[2] for problem in selected),
        evidence_refs=dedupe([problem[3] for problem in selected]),
    )


# ----------------------------------------------------------------------------- 3. entity


def entities_valid(*, referenced_refs: list[str], resolved: dict[str, Any]) -> CheckResult:
    """FAIL when a referenced entity does not exist or its state does not match.

    `resolved` maps each ref to its resolution. A ref counts as NOT FOUND when it is absent
    from the mapping, resolves to None or False, or resolves to a mapping whose `exists` is
    False. It counts as a STATE MISMATCH when it resolves to a mapping whose `state_matches`
    is False — the row exists but no longer matches the state the plan was built against.

    ENTITY_NOT_FOUND outranks ENTITY_STATE_MISMATCH: a missing row is the more serious
    defect, and reporting it first keeps the operator's first question answerable.
    """
    not_found: list[str] = []
    mismatched: list[str] = []

    for ref in referenced_refs:
        value = resolved.get(ref, _MISSING)

        if value is _MISSING or value is None or value is False:
            not_found.append(ref)
        elif isinstance(value, Mapping):
            if value.get("exists") is False:
                not_found.append(ref)
            elif value.get("state_matches") is False:
                mismatched.append(ref)

    if not_found:
        return CheckResult(
            name=CheckName.entities_valid,
            state=CheckState.failed,
            reason_code=ReasonCode.ENTITY_NOT_FOUND,
            reason=f"unresolved: {', '.join(dedupe(not_found))}",
            evidence_refs=dedupe(not_found),
        )

    if mismatched:
        return CheckResult(
            name=CheckName.entities_valid,
            state=CheckState.failed,
            reason_code=ReasonCode.ENTITY_STATE_MISMATCH,
            reason=f"state changed since planning: {', '.join(dedupe(mismatched))}",
            evidence_refs=dedupe(mismatched),
        )

    return _passed(CheckName.entities_valid)


# ----------------------------------------------------------------------------- 4. policy

#: Generic comparison operators. Jurisdiction vocabulary lives in packs, never here.
_COMPARISONS: Final[frozenset[str]] = frozenset({"eq", "ne", "lt", "lte", "gt", "gte"})
_MEMBERSHIPS: Final[frozenset[str]] = frozenset({"in", "not_in", "disjoint_from"})
_PRESENCE: Final[frozenset[str]] = frozenset({"required", "forbidden"})
_AGGREGATES: Final[frozenset[str]] = frozenset({"max_total"})

CONSTRAINT_OPERATORS: Final[frozenset[str]] = _COMPARISONS | _MEMBERSHIPS | _PRESENCE | _AGGREGATES

# Problem severity for policy compliance. Higher wins.
_RANK_SOFT_BREACH: Final = 1
_RANK_BREACH: Final = 2
_RANK_MISSING_FACT: Final = 3
_RANK_UNSATISFIABLE: Final = 4
_RANK_PACK_UNAVAILABLE: Final = 5
_RANK_UNKNOWN_OPERATOR: Final = 6


def _compare(op: str, left: Any, right: Any) -> bool | None:
    """Apply a generic operator. Returns None when the values are not comparable."""
    try:
        if op == "eq":
            return bool(left == right)
        if op == "ne":
            return bool(left != right)
        if op == "lt":
            return bool(left < right)
        if op == "lte":
            return bool(left <= right)
        if op == "gt":
            return bool(left > right)
        if op == "gte":
            return bool(left >= right)
        if op == "in":
            return left in right
        if op == "not_in":
            return left not in right
        if op == "disjoint_from":
            # Neither collection may contain anything the other does. Used to forbid a payload
            # citing a rule the pack excluded from evaluation.
            items = left if isinstance(left, list | tuple | set) else [left]
            return not (set(items) & set(right))
        if op == "max_total":
            total = sum(left) if isinstance(left, list | tuple) else left
            return bool(total <= right)
    except TypeError:
        return None
    return None


def policy_compliant(
    *, action_type: str, payload: dict[str, Any], constraints: list[dict[str, Any]]
) -> CheckResult:
    """FAIL on any breach of a business constraint or selected policy-pack constraint.

    Each constraint is `{"field": <dotted path into payload>, "op": ..., "value": ...}` with
    optional `applies_to_actions` (a list scoping it to specific action types), `soft`
    (breach yields WARN rather than FAIL) and `id` (echoed into the reason).

    Two entries carry no field and cannot be satisfied by any payload, because they report a
    determination already made upstream:

      * `pack_unavailable: true` fails with POLICY_PACK_UNAVAILABLE — a selected rule whose
        pack could not be loaded must never read as compliant.
      * `unsatisfiable: true` fails with POLICY_CONSTRAINT_BREACH and the supplied `reason`,
        for a policy block that is not shaped like a missing fact.

    An operator outside CONSTRAINT_OPERATORS is UNKNOWN_RULE_OPERATOR. This is the first
    condition in the gate's aggregation order: an unparseable constraint fails closed rather
    than being silently skipped.

    A `soft` breach returns WARN, which under the current config has no route to
    execute_flagged — only `sources_fresh` appears in `warn_allowed_actions`. WARN is
    recorded honestly and still blocks.
    """
    problems: list[tuple[int, ReasonCode, str]] = []

    for constraint in constraints:
        scope = constraint.get("applies_to_actions")
        if scope is not None and action_type not in scope:
            continue

        label = str(constraint.get("id") or constraint.get("field") or "constraint")

        if constraint.get("unsatisfiable"):
            # The policy layer already determined this cannot proceed for a reason that is not
            # shaped like a missing fact — an unresolved conflict between two entitlements, or
            # a deferral to another jurisdiction. It is carried as a constraint that cannot be
            # met, so the gate reaches the same conclusion through its normal path rather than
            # needing a special case.
            detail = str(constraint.get("reason") or "policy evaluation could not proceed")
            problems.append(
                (_RANK_UNSATISFIABLE, ReasonCode.POLICY_CONSTRAINT_BREACH, f"{label}: {detail}")
            )
            continue

        if constraint.get("pack_unavailable"):
            detail = str(constraint.get("reason") or "policy pack could not be loaded")
            problems.append(
                (_RANK_PACK_UNAVAILABLE, ReasonCode.POLICY_PACK_UNAVAILABLE, f"{label}: {detail}")
            )
            continue

        op = str(constraint.get("op", ""))
        if op not in CONSTRAINT_OPERATORS:
            problems.append(
                (
                    _RANK_UNKNOWN_OPERATOR,
                    ReasonCode.UNKNOWN_RULE_OPERATOR,
                    f"{label}: operator '{op}' is not supported",
                )
            )
            continue

        field = str(constraint.get("field", ""))
        actual = _lookup(payload, field)
        expected = constraint.get("value")
        rank = _RANK_SOFT_BREACH if constraint.get("soft") else _RANK_BREACH

        if op == "required":
            if _is_absent(actual):
                problems.append(
                    (_RANK_MISSING_FACT, ReasonCode.MISSING_REQUIRED_FACT, f"{field} is absent")
                )
            continue

        if op == "forbidden":
            if not _is_absent(actual):
                problems.append(
                    (
                        rank,
                        ReasonCode.POLICY_CONSTRAINT_BREACH,
                        f"{label}: {field} must not be present",
                    )
                )
            continue

        if _is_absent(actual):
            # A constraint cannot be shown to hold against a missing value, and assuming it
            # holds is exactly the failure direction the gate exists to prevent.
            problems.append(
                (_RANK_MISSING_FACT, ReasonCode.MISSING_REQUIRED_FACT, f"{field} is absent")
            )
            continue

        outcome = _compare(op, actual, expected)
        if outcome is None:
            problems.append(
                (
                    rank,
                    ReasonCode.POLICY_CONSTRAINT_BREACH,
                    f"{label}: {field}={actual!r} is not comparable to {expected!r} via '{op}'",
                )
            )
        elif not outcome:
            problems.append(
                (
                    rank,
                    ReasonCode.POLICY_CONSTRAINT_BREACH,
                    f"{label}: {field}={actual!r} violates {op} {expected!r}",
                )
            )

    if not problems:
        return _passed(CheckName.policy_compliant)

    worst_rank = max(rank for rank, *_ in problems)
    selected = [problem for problem in problems if problem[0] == worst_rank]
    state = CheckState.warn if worst_rank == _RANK_SOFT_BREACH else CheckState.failed

    return CheckResult(
        name=CheckName.policy_compliant,
        state=state,
        reason_code=selected[0][1],
        reason="; ".join(problem[2] for problem in selected),
    )


# --------------------------------------------------------------------------- 5. conflicts

#: A prior attempt in one of these states does not block a fresh attempt — otherwise one
#: failed rebooking would permanently wedge the passenger. Any other state, including an
#: unrecognised one, blocks.
_NON_BLOCKING_STATES: Final[frozenset[str]] = frozenset(
    {TaskState.failed.value, TaskState.rejected.value, TaskState.skipped.value}
)


def no_conflicts(
    *, action_type: str, target_refs: list[str], pending_or_executed: list[dict[str, Any]]
) -> CheckResult:
    """FAIL on a duplicate action or on consuming unavailable capacity.

    This is what prevents a double-booked room or a twice-rebooked passenger.

    `pending_or_executed` carries two entry shapes:

      * prior work — `{"action_type": ..., "target_refs": [...], "state": ...}`. It conflicts
        when the action type matches and the target refs overlap, unless its state is
        failed, rejected or skipped. An unrecognised state blocks.
      * capacity — `{"kind": "capacity", "ref": ..., "available": <int>}`. It conflicts when
        a target ref matches and nothing is available. `available: None` also conflicts,
        because unknown capacity is not spare capacity.

    An entry that declares no target refs is treated as unscoped and therefore overlapping:
    we cannot rule out a collision, so we fail closed.

    DUPLICATE_ACTION outranks CAPACITY_UNAVAILABLE.
    """
    requested = dedupe(target_refs)
    duplicates: list[str] = []
    exhausted: list[str] = []

    for entry in pending_or_executed:
        if entry.get("kind") == "capacity":
            ref = str(entry.get("ref", ""))
            if ref not in requested:
                continue
            available = entry.get("available", entry.get("capacity_available"))
            if available is None or available <= 0:
                exhausted.append(ref)
            continue

        if entry.get("action_type") != action_type:
            continue

        state = str(entry.get("state", entry.get("status", "")))
        if state in _NON_BLOCKING_STATES:
            continue

        existing = entry.get("target_refs") or []
        if not existing or not requested:
            duplicates.extend(requested or ["*"])
            continue

        duplicates.extend(ref for ref in requested if ref in existing)

    if duplicates:
        collisions = dedupe(duplicates)
        return CheckResult(
            name=CheckName.no_conflicts,
            state=CheckState.failed,
            reason_code=ReasonCode.DUPLICATE_ACTION,
            reason=f"{action_type} already outstanding for {', '.join(collisions)}",
            evidence_refs=collisions,
        )

    if exhausted:
        collisions = dedupe(exhausted)
        return CheckResult(
            name=CheckName.no_conflicts,
            state=CheckState.failed,
            reason_code=ReasonCode.CAPACITY_UNAVAILABLE,
            reason=f"no capacity available for {', '.join(collisions)}",
            evidence_refs=collisions,
        )

    return _passed(CheckName.no_conflicts)


# --------------------------------------------------------------------------- 6. risk tier


def action_risk(*, action_type: str, config: AssuranceConfig) -> CheckResult:
    """Classify risk. Sets `tier`; may PASS while its tier still forces human approval.

    An action type absent from config.risk_tiers is HIGH. Unknown means dangerous.

    This check NEVER returns FAIL. It classifies, it does not judge: a high tier blocks
    through the gate's aggregation order, not by failing here. Preserving that distinction is
    why the contract keeps three states instead of a boolean — the audit record shows a check
    that passed on an action that was still refused.
    """
    tier = config.tier_for(action_type)

    if tier is RiskTier.high:
        return CheckResult(
            name=CheckName.action_risk,
            state=CheckState.passed,
            reason_code=ReasonCode.HUMAN_APPROVAL_REQUIRED,
            reason=(
                f"'{action_type}' is not in the configured risk tiers, so it is treated as high"
                if action_type not in config.risk_tiers
                else f"'{action_type}' is high risk and always requires human approval"
            ),
            tier=tier,
        )

    return _passed(CheckName.action_risk, tier=tier)
