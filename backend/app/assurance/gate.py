"""Fail-closed aggregation — STREAM B.

This module is the authorisation boundary of the whole system. `evaluate` is the canonical
entry point: Stream A hands it the facts it has gathered, and it runs the six checks and
combines them. Nothing outside this module decides whether an action may execute, and no
caller should assemble the six checks itself.

The aggregation ORDER is a contract. Implemented exactly:

    1. Missing config, unknown action type, or unknown rule operator -> FAIL
    2. Any FAIL                -> needs_human. Nothing executes.
    3. risk_tier == high       -> needs_human even when every check passes
    4. A WARN -> execute_flagged ONLY when the versioned config explicitly permits that
       warning for that action. There is no global soft-failure bypass.
    5. Otherwise -> execute. Multiple warnings never become safer by aggregation.

The result is immutable. A corrected decision requires a NEW evaluation, never an update.

`config_version` and `config_hash` are stamped on every evaluation so a replay uses the
semantics that applied at decision time. The hash is the SHA-256 of the config file's bytes,
truncated to sixteen characters, matching what `app.config` reports on GET /system/mode —
an evaluation and the mode endpoint must never disagree about which config was in force.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.assurance.checks import (
    action_risk,
    dedupe,
    entities_valid,
    evidence_complete,
    no_conflicts,
    policy_compliant,
    sources_fresh,
)
from app.assurance.contract import (
    CHECK_ORDER,
    AssuranceConfig,
    AssuranceResult,
    CheckName,
    CheckResult,
    ReasonCode,
)
from app.config import resolve_repo_path
from app.errors import AssuranceConfigMissing
from app.models.enums import ActionType, AssuranceDecision, CheckState, RiskTier

#: Stamped when the gate could not read its own configuration. A record that cannot name the
#: semantics it was decided under says so, rather than implying a version it never loaded.
CONFIG_UNAVAILABLE: Final = "unavailable"

#: Sections of a versioned gate config that belong to the PLAN level, validated by
#: `app.assurance.plan_gate.load_plan_config`. Listed here so one file can serve both levels:
#: duplicating the action-level blocks into a second file would create two sources of truth for
#: `risk_tiers`, and the copy nobody loads is the one that eventually disagrees.
_PLAN_LEVEL_SECTIONS: Final[frozenset[str]] = frozenset({"plan", "what_if"})

#: Aggregation rule 1. These three conditions are FAIL regardless of the state a check
#: reported, so the rule holds even if a check is later written to be more forgiving.
_HARD_FAIL_CODES: Final[frozenset[ReasonCode]] = frozenset(
    {
        ReasonCode.CONFIG_MISSING,
        ReasonCode.UNKNOWN_ACTION_TYPE,
        ReasonCode.UNKNOWN_RULE_OPERATOR,
    }
)

_SEVERITY: Final[dict[CheckState, int]] = {
    CheckState.passed: 0,
    CheckState.warn: 1,
    CheckState.failed: 2,
}

_KNOWN_ACTION_TYPES: Final[frozenset[str]] = frozenset(member.value for member in ActionType)

#: Actions whose payload asserts a legal entitlement, and which therefore may not be
#: authorised without policy-derived requirements. For these, empty `required_facts` and empty
#: `constraints` are a defect rather than a clean sheet: both policy checks would pass without
#: verifying anything, and the record would show two green ticks that mean nothing.
#:
#: Requirements come from `app.policy.requirements.gate_requirements`. Adding an action here is
#: a reviewed change, because it makes that call mandatory for the action.
POLICY_BEARING_ACTIONS: Final[frozenset[str]] = frozenset({ActionType.evaluate_entitlements.value})

#: Keys of `GateInputs` a caller may supply as a plain mapping. Anything else in that mapping
#: is treated as the proposed action's payload, which is what an unrecognised planner key is.
_GATE_INPUT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "required_facts",
        "provided_facts",
        "sources",
        "referenced_refs",
        "resolved_entities",
        "payload",
        "constraints",
        "target_refs",
        "pending_or_executed",
        "extra_evidence_refs",
    }
)


class GateInputs(BaseModel):
    """Everything the six checks need, gathered by the caller.

    The checks are pure, so this is the whole of their world: no check reaches back for a
    database row, a provider response or the clock.
    """

    model_config = ConfigDict(extra="forbid")

    action_type: str

    # 1. evidence_complete — dotted paths the selected rule declares, and the facts gathered.
    required_facts: list[str] = Field(default_factory=list)
    provided_facts: dict[str, Any] = Field(default_factory=dict)

    # 2. sources_fresh — "<kind>:<identifier>" -> observation timestamp, or None if undated.
    sources: dict[str, datetime | None] = Field(default_factory=dict)

    # 3. entities_valid — refs the plan names, and how each resolved.
    referenced_refs: list[str] = Field(default_factory=list)
    resolved_entities: dict[str, Any] = Field(default_factory=dict)

    # 4. policy_compliant — the proposed action's payload and the constraints selected for it.
    payload: dict[str, Any] = Field(default_factory=dict)
    constraints: list[dict[str, Any]] = Field(default_factory=list)

    # 5. no_conflicts — what this action would touch, and what already touches it.
    target_refs: list[str] = Field(default_factory=list)
    pending_or_executed: list[dict[str, Any]] = Field(default_factory=list)

    # Recorded on the evaluation for audit; not consumed by any check.
    extra_evidence_refs: list[str] = Field(default_factory=list)

    @classmethod
    def from_task(
        cls,
        *,
        action_type: str,
        inputs: Mapping[str, Any] | None = None,
        target_refs: list[str] | None = None,
        evidence_refs: list[str] | None = None,
    ) -> GateInputs:
        """Build inputs from an orchestrator task.

        Recognised keys are read as the corresponding gate input; every other key becomes part
        of the proposed action's `payload`, because that is what an unrecognised planner key is.
        Doing the split here keeps `extra="forbid"` meaningful — a caller still cannot invent a
        gate input — while not requiring the planner to know the gate's field names.
        """
        supplied = dict(inputs or {})
        known = {key: value for key, value in supplied.items() if key in _GATE_INPUT_KEYS}
        remainder = {key: value for key, value in supplied.items() if key not in _GATE_INPUT_KEYS}

        payload = {**(known.pop("payload", None) or {}), **remainder}

        return cls(
            action_type=action_type,
            payload=payload,
            target_refs=known.pop("target_refs", None) or list(target_refs or []),
            extra_evidence_refs=known.pop("extra_evidence_refs", None) or list(evidence_refs or []),
            **known,
        )


def _coerce(check: CheckResult) -> CheckResult:
    """Force aggregation rule 1: the three hard-fail codes are FAIL, whatever was reported."""
    if check.reason_code in _HARD_FAIL_CODES and check.state is not CheckState.failed:
        return check.model_copy(update={"state": CheckState.failed})
    return check


def _did_not_run(name: CheckName) -> CheckResult:
    """A check that did not run cannot authorise anything.

    MISSING_EVIDENCE is used literally: there is no evidence this check was performed.
    """
    return CheckResult(
        name=name,
        state=CheckState.failed,
        reason_code=ReasonCode.MISSING_EVIDENCE,
        reason=f"{name.value} did not run",
    )


def _ordered(checks: list[CheckResult]) -> list[CheckResult]:
    """Return exactly six checks in CHECK_ORDER, so the UI and the audit record agree.

    An absent check becomes a FAIL. A duplicated check keeps its worst state, because the
    safe reading of two conflicting reports is the more severe one.
    """
    worst: dict[CheckName, CheckResult] = {}
    for check in checks:
        candidate = _coerce(check)
        held = worst.get(candidate.name)
        if held is None or _SEVERITY[candidate.state] > _SEVERITY[held.state]:
            worst[candidate.name] = candidate

    return [worst.get(name) or _did_not_run(name) for name in CHECK_ORDER]


def _config_fingerprint(config: AssuranceConfig) -> str:
    """Fallback identity for a config that did not come from a file on disk.

    Prefixed so it can never be mistaken for the file digest reported by /system/mode.
    """
    payload = config.model_dump_json()
    return f"content:{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"


def aggregate(
    *,
    checks: list[CheckResult],
    action_type: str,
    config: AssuranceConfig | None,
    config_hash: str | None = None,
) -> AssuranceResult:
    """Combine check results into a single authorisation decision.

    `config` is widened to accept None so a caller that failed to load configuration cannot
    reach a permissive path by accident. `config_hash` should be the file digest from
    `load_config_with_digest` (or `ResolvedModes.assurance_config_hash`); when omitted, a
    content fingerprint of the config object is stamped instead.
    """
    # ---------------------------------------------------------- rule 1: missing config
    if config is None:
        blocked = [
            CheckResult(
                name=name,
                state=CheckState.failed,
                reason_code=ReasonCode.CONFIG_MISSING,
                reason="gate configuration unavailable, so no check was performed",
            )
            for name in CHECK_ORDER
        ]
        return AssuranceResult(
            decision=AssuranceDecision.needs_human,
            risk_tier=RiskTier.high,
            checks=blocked,
            blocking=list(CHECK_ORDER),
            config_version=CONFIG_UNAVAILABLE,
            config_hash=CONFIG_UNAVAILABLE,
        )

    version = config.version
    digest = config_hash or _config_fingerprint(config)
    ordered = _ordered(checks)

    # ------------------------------------------------- rule 1: unknown action type
    # ActionType is a closed set and Stream A rejects unknown actions before assurance, so
    # reaching here means something bypassed that. It is a defect, not a risk classification.
    if action_type not in _KNOWN_ACTION_TYPES:
        rejected = CheckResult(
            name=CheckName.action_risk,
            state=CheckState.failed,
            reason_code=ReasonCode.UNKNOWN_ACTION_TYPE,
            reason=f"'{action_type}' is not a known action type",
            tier=RiskTier.high,
        )
        ordered = [rejected if check.name is CheckName.action_risk else check for check in ordered]
        return AssuranceResult(
            decision=AssuranceDecision.needs_human,
            risk_tier=RiskTier.high,
            checks=ordered,
            blocking=[name for name in CHECK_ORDER if _is_blocking(ordered, name)],
            evidence_refs=_collect_refs(ordered),
            config_version=version,
            config_hash=digest,
        )

    # The classification of record is what `action_risk` reported; the config is the fallback
    # when that check was not supplied.
    classified = next((c for c in ordered if c.name is CheckName.action_risk), None)
    tier = classified.tier if classified and classified.tier else config.tier_for(action_type)

    failures = [check for check in ordered if check.state is CheckState.failed]
    warnings = [check for check in ordered if check.state is CheckState.warn]

    high_risk_blocks = tier is RiskTier.high and config.high_risk_requires_human
    blocking: list[CheckName] = [check.name for check in failures]

    # ---------------------------------------------------------- rule 2: any FAIL blocks
    if failures:
        decision = AssuranceDecision.needs_human
        if high_risk_blocks and CheckName.action_risk not in blocking:
            blocking.append(CheckName.action_risk)

    # ------------------------------ rule 3: high risk blocks even when everything passes
    elif high_risk_blocks:
        decision = AssuranceDecision.needs_human
        blocking = [CheckName.action_risk]

    # ------------------------------------- rule 4: a WARN needs explicit config permission
    elif warnings:
        unpermitted = [
            check.name for check in warnings if not config.warn_permitted(action_type, check.name)
        ]
        if unpermitted:
            # Multiple warnings never become safer by aggregation: one unpermitted warning
            # blocks regardless of how many others were tolerated.
            decision = AssuranceDecision.needs_human
            blocking = unpermitted
        else:
            decision = AssuranceDecision.execute_flagged

    # ------------------------------------------------------------------ rule 5: otherwise
    else:
        decision = AssuranceDecision.execute

    return AssuranceResult(
        decision=decision,
        risk_tier=tier,
        checks=ordered,
        blocking=[name for name in CHECK_ORDER if name in blocking],
        evidence_refs=_collect_refs(ordered),
        config_version=version,
        config_hash=digest,
    )


def _is_blocking(checks: list[CheckResult], name: CheckName) -> bool:
    return any(check.name is name and check.state is CheckState.failed for check in checks)


def _collect_refs(checks: list[CheckResult]) -> list[str]:
    return dedupe([ref for check in checks for ref in check.evidence_refs])


_VACUITY_GUARDED: Final[dict[CheckName, tuple[str, ReasonCode]]] = {
    CheckName.evidence_complete: ("required_facts", ReasonCode.MISSING_REQUIRED_FACT),
    CheckName.policy_compliant: ("constraints", ReasonCode.POLICY_CONSTRAINT_BREACH),
}

_DERIVE_REQUIREMENTS_HINT: Final = (
    "call app.policy.requirements.gate_requirements() and pass its required_facts and constraints"
)


def _refuse_vacuous(checks: list[CheckResult], inputs: GateInputs) -> list[CheckResult]:
    """Refuse a policy-bearing action whose policy checks verified nothing.

    `evidence_complete` with no required facts and `policy_compliant` with no constraints both
    return PASS. For most actions that is honest — a hotel reservation has no statutory facts to
    check. For an action that asserts a legal entitlement it is a defect: the requirements were
    never derived, and the record would show two green ticks that mean nothing.

    A check that already failed on its own merits is left exactly as it is. Overwriting it would
    replace a precise diagnosis, such as POLICY_PACK_UNAVAILABLE, with a generic one.
    """
    if inputs.action_type not in POLICY_BEARING_ACTIONS:
        return checks

    refused: list[CheckResult] = []
    for check in checks:
        guarded = _VACUITY_GUARDED.get(check.name)
        if guarded is None or check.state is not CheckState.passed:
            refused.append(check)
            continue

        field, code = guarded
        if getattr(inputs, field):
            refused.append(check)
            continue

        refused.append(
            CheckResult(
                name=check.name,
                state=CheckState.failed,
                reason_code=code,
                reason=(
                    f"no {field} supplied for '{inputs.action_type}', so this check verified "
                    f"nothing; {_DERIVE_REQUIREMENTS_HINT}"
                ),
            )
        )
    return refused


def evaluate(
    *,
    config: AssuranceConfig | None,
    inputs: GateInputs | Mapping[str, Any] | None = None,
    config_hash: str | None = None,
    now: datetime | None = None,
    action_type: str | None = None,
    target_refs: list[str] | None = None,
    evidence_refs: list[str] | None = None,
    incident_state: str | None = None,
) -> AssuranceResult:
    """Run the Decision Assurance Gate. The canonical entry point for authorisation.

    Runs the six checks in CHECK_ORDER against `inputs`, then applies the fail-closed
    aggregation order. Callers must not assemble or interpret the checks themselves.

    Two call shapes, the same evaluation:

        evaluate(inputs=GateInputs(...), config=config, config_hash=digest, now=moment)

        evaluate(action_type=..., target_refs=[...], inputs={...},
                 evidence_refs=[...], incident_state=..., config=config)

    The second is the orchestrator adapter's interface, where `inputs` is the planner's task
    dictionary; recognised keys become gate inputs and the rest becomes the action payload.
    `incident_state` is accepted for interface compatibility and is not consumed: the gate
    authorises an action on evidence, and the state machine is the orchestrator's to enforce.

    Pass `now` explicitly to keep an evaluation reproducible; it defaults to the current UTC
    time for convenience. `config_hash` should be the digest from `load_config_with_digest`
    so the record matches GET /system/mode.

    A `config` of None yields needs_human with all six checks FAIL: the gate cannot authorise
    anything it has no configuration for.
    """
    if isinstance(inputs, GateInputs):
        resolved = inputs
    else:
        if action_type is None:
            raise TypeError("evaluate() requires either inputs=GateInputs(...) or action_type=")
        resolved = GateInputs.from_task(
            action_type=action_type,
            inputs=inputs,
            target_refs=target_refs,
            evidence_refs=evidence_refs,
        )

    if config is None:
        return aggregate(
            checks=[], action_type=resolved.action_type, config=None, config_hash=config_hash
        )

    inputs = resolved
    moment = now or datetime.now(UTC)

    checks = [
        evidence_complete(
            required_facts=inputs.required_facts, provided_facts=inputs.provided_facts
        ),
        sources_fresh(
            sources=inputs.sources, now=moment, config=config, action_type=inputs.action_type
        ),
        entities_valid(referenced_refs=inputs.referenced_refs, resolved=inputs.resolved_entities),
        policy_compliant(
            action_type=inputs.action_type, payload=inputs.payload, constraints=inputs.constraints
        ),
        no_conflicts(
            action_type=inputs.action_type,
            target_refs=inputs.target_refs,
            pending_or_executed=inputs.pending_or_executed,
        ),
        action_risk(action_type=inputs.action_type, config=config),
    ]

    checks = _refuse_vacuous(checks, inputs)

    result = aggregate(
        checks=checks,
        action_type=inputs.action_type,
        config=config,
        config_hash=config_hash,
    )

    # The refs a decision was made against belong on the record, not only the ones a check
    # happened to complain about. model_copy builds a new record rather than mutating one.
    refs = dedupe(
        [
            *result.evidence_refs,
            *inputs.referenced_refs,
            *inputs.target_refs,
            *inputs.extra_evidence_refs,
        ]
    )
    if refs != result.evidence_refs:
        result = result.model_copy(update={"evidence_refs": refs})
    return result


def load_config_with_digest(path: str | Path) -> tuple[AssuranceConfig, str]:
    """Load versioned gate config and the digest of the exact bytes it came from.

    One read, so the config and its hash can never describe different file contents.
    """
    resolved = resolve_repo_path(Path(path))

    if not resolved.is_file():
        raise AssuranceConfigMissing(
            f"assurance config not found at {resolved}; no action can be authorised",
            details={"path": str(resolved), "reason_code": ReasonCode.CONFIG_MISSING.value},
        )

    raw = resolved.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()[:16]

    try:
        parsed = yaml.safe_load(raw.decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise AssuranceConfigMissing(
            f"assurance config at {resolved} is not readable YAML; no action can be authorised",
            details={"path": str(resolved), "reason_code": ReasonCode.CONFIG_MISSING.value},
        ) from exc

    if not isinstance(parsed, dict):
        raise AssuranceConfigMissing(
            f"assurance config at {resolved} is not a mapping; no action can be authorised",
            details={"path": str(resolved), "reason_code": ReasonCode.CONFIG_MISSING.value},
        )

    # Plan-level sections belong to the same versioned file but are read by
    # `app.assurance.plan_gate.load_plan_config`, which validates them properly. Removing them
    # here lets ONE config file serve both levels instead of duplicating `risk_tiers`,
    # `freshness` and `warn_allowed_actions` into a second file that would drift. `extra="forbid"`
    # still applies to everything else, so a genuine typo remains an error.
    action_level = {key: value for key, value in parsed.items() if key not in _PLAN_LEVEL_SECTIONS}

    try:
        # extra="forbid" on AssuranceConfig means an unrecognised key is an error rather than
        # a setting that silently does nothing. A safety config must not contain a typo that
        # reads as permissive.
        config = AssuranceConfig.model_validate(action_level)
    except ValidationError as exc:
        raise AssuranceConfigMissing(
            f"assurance config at {resolved} is invalid; no action can be authorised",
            details={
                "path": str(resolved),
                "reason_code": ReasonCode.CONFIG_MISSING.value,
                "errors": exc.errors(include_url=False),
            },
        ) from exc

    return config, digest


def load_config(path: str) -> AssuranceConfig:
    """Load and validate versioned gate config.

    A missing or unparseable file raises AssuranceConfigMissing so the caller blocks
    execution. Returning a permissive default here would defeat the entire design: the system
    would look healthy while authorising actions against semantics nobody wrote down.
    """
    config, _ = load_config_with_digest(path)
    return config
