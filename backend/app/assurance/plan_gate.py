"""Plan-level fail-closed aggregation — STREAM B, new in Phase 2.

`evaluate_plan` is the canonical plan-level entry point. The aggregation order is the same
contract as the action gate, one level up:

    1. Missing plan config -> FAIL
    2. Any FAIL                 -> needs_human. The plan is not admissible.
    3. plan_risk == high        -> needs_human even when every check passes
    4. A WARN -> execute_flagged ONLY where the versioned config permits that check
    5. Otherwise -> execute

**A blocked plan admits nothing, including its individually-safe tasks.** Partial execution of an
inconsistent plan is how a passenger gets rebooked and never told. The operator either accepts the
plan's aggregate risk or the planner produces a different plan.

**Admission is not authorisation.** `execute` here means the plan may proceed to per-action
authorisation. Every task still passes the action gate at execution time, because state moves
between planning and execution.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Final

import yaml
from pydantic import ValidationError

from app.assurance.blocking import blocking_kinds, is_approvable
from app.assurance.checks import dedupe
from app.assurance.contract import AssuranceResult
from app.assurance.plan_checks import (
    coverage_complete,
    dependencies_sound,
    exposure_within_limits,
    plan_consistent,
    plan_risk,
    tasks_authorised,
)
from app.assurance.plan_contract import (
    PLAN_CHECK_ORDER,
    PLAN_CONFIG_UNAVAILABLE,
    CoverageDeclaration,
    ExposureInputs,
    PlanAssuranceResult,
    PlanCheckName,
    PlanCheckResult,
    PlanConfig,
    PlanReasonCode,
    PlanUnderReview,
    TaskOutcome,
    WhatIfPolicy,
)
from app.config import resolve_repo_path
from app.errors import AssuranceConfigMissing
from app.models.enums import AssuranceDecision, CheckState, RiskTier

_SEVERITY: Final[dict[CheckState, int]] = {
    CheckState.passed: 0,
    CheckState.warn: 1,
    CheckState.failed: 2,
}


def task_outcome_from(
    *,
    task_id: str,
    action_type: str,
    result: AssuranceResult,
    target_refs: list[str] | None = None,
    depends_on: list[str] | None = None,
    evaluation_id: int | None = None,
) -> TaskOutcome:
    """Project an action-level result into what the plan gate needs.

    Provided so no caller has to work out how to classify a block: the derivation lives in
    app.assurance.blocking and is applied here once.
    """
    return TaskOutcome(
        task_id=task_id,
        action_type=action_type,
        target_refs=list(target_refs or []),
        depends_on=list(depends_on or []),
        decision=result.decision,
        risk_tier=result.risk_tier,
        blocking_kinds=list(blocking_kinds(result)),
        approvable=is_approvable(result),
        evaluation_id=evaluation_id,
    )


def _did_not_run(name: PlanCheckName, reason: str) -> PlanCheckResult:
    return PlanCheckResult(
        name=name,
        state=CheckState.failed,
        reason_code=PlanReasonCode.PLAN_CONFIG_MISSING,
        reason=reason,
    )


def _ordered(checks: list[PlanCheckResult]) -> list[PlanCheckResult]:
    """Exactly six checks in PLAN_CHECK_ORDER. Absent is FAIL; duplicated keeps the worst."""
    worst: dict[PlanCheckName, PlanCheckResult] = {}
    for check in checks:
        held = worst.get(check.name)
        if held is None or _SEVERITY[check.state] > _SEVERITY[held.state]:
            worst[check.name] = check

    return [
        worst.get(name) or _did_not_run(name, f"{name.value} did not run")
        for name in PLAN_CHECK_ORDER
    ]


def aggregate_plan(
    *,
    checks: list[PlanCheckResult],
    plan: PlanUnderReview,
    config: PlanConfig | None,
    config_version: str,
    config_hash: str,
    exposure: ExposureInputs | None = None,
) -> PlanAssuranceResult:
    """Combine plan checks into one admission decision."""
    plan_hash = plan.hash()
    evaluation_ids = [task.evaluation_id for task in plan.tasks if task.evaluation_id is not None]
    exposure_record = exposure.model_dump() if exposure else {}

    # ----------------------------------------------------------- rule 1: missing config
    if config is None:
        blocked = [
            _did_not_run(name, "plan configuration unavailable, so no plan check was performed")
            for name in PLAN_CHECK_ORDER
        ]
        return PlanAssuranceResult(
            decision=AssuranceDecision.needs_human,
            plan_risk_tier=RiskTier.high,
            checks=blocked,
            blocking=list(PLAN_CHECK_ORDER),
            group_reference=plan.group_reference,
            plan_id=plan.plan_id,
            plan_hash=plan_hash,
            task_count=len(plan.tasks),
            task_evaluation_ids=evaluation_ids,
            exposure=exposure_record,
            config_version=PLAN_CONFIG_UNAVAILABLE,
            config_hash=PLAN_CONFIG_UNAVAILABLE,
        )

    ordered = _ordered(checks)

    classified = next((c for c in ordered if c.name is PlanCheckName.plan_risk), None)
    tier = classified.tier if classified and classified.tier else RiskTier.high

    failures = [check for check in ordered if check.state is CheckState.failed]
    warnings = [check for check in ordered if check.state is CheckState.warn]
    high_risk_blocks = tier is RiskTier.high

    blocking: list[PlanCheckName] = [check.name for check in failures]

    # -------------------------------------------------------------- rule 2: any FAIL blocks
    if failures:
        decision = AssuranceDecision.needs_human
        if high_risk_blocks and PlanCheckName.plan_risk not in blocking:
            blocking.append(PlanCheckName.plan_risk)

    # ----------------------------- rule 3: a high-risk plan blocks even when all checks pass
    elif high_risk_blocks:
        decision = AssuranceDecision.needs_human
        blocking = [PlanCheckName.plan_risk]

    # ------------------------------------ rule 4: a WARN needs explicit config permission
    elif warnings:
        unpermitted = [check.name for check in warnings if not config.warn_permitted(check.name)]
        if unpermitted:
            decision = AssuranceDecision.needs_human
            blocking = unpermitted
        else:
            decision = AssuranceDecision.execute_flagged

    # ------------------------------------------------------------------- rule 5: otherwise
    else:
        decision = AssuranceDecision.execute

    return PlanAssuranceResult(
        decision=decision,
        plan_risk_tier=tier,
        checks=ordered,
        blocking=[name for name in PLAN_CHECK_ORDER if name in blocking],
        group_reference=plan.group_reference,
        plan_id=plan.plan_id,
        plan_hash=plan_hash,
        task_count=len(plan.tasks),
        task_evaluation_ids=evaluation_ids,
        exposure=exposure_record,
        config_version=config_version,
        config_hash=config_hash,
    )


def evaluate_plan(
    *,
    plan: PlanUnderReview,
    coverage: CoverageDeclaration,
    exposure: ExposureInputs,
    config: PlanConfig | None,
    config_version: str = PLAN_CONFIG_UNAVAILABLE,
    config_hash: str = PLAN_CONFIG_UNAVAILABLE,
) -> PlanAssuranceResult:
    """Run the six plan checks and aggregate. The canonical plan-level entry point.

    Deterministic: the same plan, coverage, exposure and config always produce the same decision.
    No clock read, no database, no provider.
    """
    if config is None:
        return aggregate_plan(
            checks=[],
            plan=plan,
            config=None,
            config_version=config_version,
            config_hash=config_hash,
            exposure=exposure,
        )

    checks = [
        tasks_authorised(tasks=plan.tasks),
        dependencies_sound(tasks=plan.tasks),
        plan_consistent(tasks=plan.tasks, config=config),
        coverage_complete(tasks=plan.tasks, coverage=coverage),
        exposure_within_limits(tasks=plan.tasks, exposure=exposure, config=config),
        plan_risk(tasks=plan.tasks, exposure=exposure, config=config),
    ]

    return aggregate_plan(
        checks=checks,
        plan=plan,
        config=config,
        config_version=config_version,
        config_hash=config_hash,
        exposure=exposure,
    )


class LoadedPlanConfig:
    """The plan-level slice of a versioned gate config, with the digest it came from."""

    __slots__ = ("digest", "plan", "version", "what_if")

    def __init__(
        self, *, version: str, digest: str, plan: PlanConfig, what_if: WhatIfPolicy
    ) -> None:
        self.version = version
        self.digest = digest
        self.plan = plan
        self.what_if = what_if


def load_plan_config(path: str | Path) -> LoadedPlanConfig:
    """Load the plan and what-if sections of a versioned gate config.

    Raises AssuranceConfigMissing when the file is absent, unreadable, invalid, or carries no
    `plan` section. A config that predates plan-level assurance cannot authorise a plan, and
    silently defaulting the limits would invent a budget nobody approved.
    """
    resolved = resolve_repo_path(Path(path))

    if not resolved.is_file():
        raise AssuranceConfigMissing(
            f"assurance config not found at {resolved}; no plan can be admitted",
            details={
                "path": str(resolved),
                "reason_code": PlanReasonCode.PLAN_CONFIG_MISSING.value,
            },
        )

    raw = resolved.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()[:16]

    try:
        parsed: Any = yaml.safe_load(raw.decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise AssuranceConfigMissing(
            f"assurance config at {resolved} is not readable YAML; no plan can be admitted",
            details={"path": str(resolved)},
        ) from exc

    if not isinstance(parsed, dict):
        raise AssuranceConfigMissing(
            f"assurance config at {resolved} is not a mapping; no plan can be admitted",
            details={"path": str(resolved)},
        )

    if "plan" not in parsed:
        raise AssuranceConfigMissing(
            f"assurance config at {resolved} has no `plan` section, so it predates plan-level "
            "assurance; defaulting the limits would invent a budget nobody approved",
            details={"path": str(resolved), "version": str(parsed.get("version"))},
        )

    try:
        plan = PlanConfig.model_validate(parsed["plan"])
        what_if = WhatIfPolicy.model_validate(parsed.get("what_if") or {})
    except ValidationError as exc:
        raise AssuranceConfigMissing(
            f"plan configuration at {resolved} is invalid; no plan can be admitted",
            details={"path": str(resolved), "errors": exc.errors(include_url=False)},
        ) from exc

    return LoadedPlanConfig(
        version=str(parsed.get("version") or PLAN_CONFIG_UNAVAILABLE),
        digest=digest,
        plan=plan,
        what_if=what_if,
    )


def blocking_summary(result: PlanAssuranceResult) -> list[str]:
    """Reason codes standing in the way, for an operator to act on."""
    return dedupe(
        [check.reason_code.value for check in result.checks if check.state is CheckState.failed]
    )
