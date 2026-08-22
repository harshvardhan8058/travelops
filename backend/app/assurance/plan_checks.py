"""The six plan checks — STREAM B, new in Phase 2.

Pure functions, like the action-level six. Everything arrives in arguments; nothing reads a
clock, a database or a provider. That is what makes a plan evaluation reproducible.

Each check earns its place by catching something that is invisible one action at a time:

    tasks_authorised        a task carries a block a human cannot approve away
    dependencies_sound      the task graph is unrunnable, or depends on something broken
    plan_consistent         two tasks inside one plan contradict each other
    coverage_complete       the plan silently drops part of the impacted set
    exposure_within_limits  the aggregate commits more than the configured budget
    plan_risk               classification; may PASS while its tier still forces approval

Stream B never derives an operational figure. Exposure and coverage arrive from Stream C's cascade
records; these checks only compare them against versioned limits.
"""

from __future__ import annotations

from app.assurance.checks import dedupe
from app.assurance.plan_contract import (
    CoverageDeclaration,
    ExposureInputs,
    PlanCheckName,
    PlanCheckResult,
    PlanConfig,
    PlanReasonCode,
    TaskOutcome,
)
from app.models.enums import CheckState, RiskTier


def _passed(name: PlanCheckName, *, tier: RiskTier | None = None) -> PlanCheckResult:
    return PlanCheckResult(
        name=name, state=CheckState.passed, reason_code=PlanReasonCode.OK, tier=tier
    )


def _failed(
    name: PlanCheckName,
    code: PlanReasonCode,
    reason: str,
    refs: list[str] | None = None,
) -> PlanCheckResult:
    return PlanCheckResult(
        name=name,
        state=CheckState.failed,
        reason_code=code,
        reason=reason,
        offending_refs=dedupe(refs or []),
    )


# ------------------------------------------------------------------------ 1. authorisation


def tasks_authorised(*, tasks: list[TaskOutcome]) -> PlanCheckResult:
    """FAIL when any task carries a block a human cannot approve away.

    A task needing approval purely because of its risk tier is NOT a failure here: that is the
    normal path, and it gets its own action-level decision. A task blocked on evidence or conflict
    is different — the plan cannot execute as a unit no matter who approves it, so admitting the
    plan would be admitting a plan that cannot run.

    An empty plan fails. A plan with nothing in it is not a recovery.
    """
    if not tasks:
        return _failed(
            PlanCheckName.tasks_authorised, PlanReasonCode.PLAN_EMPTY, "the plan contains no tasks"
        )

    unevaluated = [task.task_id for task in tasks if not task.blocking_kinds and task.needs_human]
    if unevaluated:
        # needs_human with no recorded reason means the projection was built without a real
        # evaluation. Treat it as absent rather than as a risk-only block.
        return _failed(
            PlanCheckName.tasks_authorised,
            PlanReasonCode.TASK_EVALUATION_MISSING,
            f"{len(unevaluated)} task(s) report needs_human with no blocking reason recorded",
            unevaluated,
        )

    hard_blocked = [task.task_id for task in tasks if task.blocked_on_evidence_or_conflict]
    if hard_blocked:
        return _failed(
            PlanCheckName.tasks_authorised,
            PlanReasonCode.TASK_NOT_AUTHORISED,
            f"{len(hard_blocked)} task(s) blocked on evidence or conflict, which approval cannot "
            f"release: {', '.join(hard_blocked)}",
            hard_blocked,
        )

    return _passed(PlanCheckName.tasks_authorised)


# -------------------------------------------------------------------------- 2. dependencies


def dependencies_sound(*, tasks: list[TaskOutcome]) -> PlanCheckResult:
    """FAIL on an unknown dependency, a cycle, or a dependency on a hard-blocked task.

    Order matters operationally: a notification that depends on a rebooking must not be sent when
    the rebooking cannot happen. A cycle means the plan can never start at all.
    """
    known = {task.task_id for task in tasks}
    hard_blocked = {task.task_id for task in tasks if task.blocked_on_evidence_or_conflict}

    unknown: list[str] = []
    blocked_deps: list[str] = []
    for task in tasks:
        for dependency in task.depends_on:
            if dependency not in known:
                unknown.append(f"{task.task_id}->{dependency}")
            elif dependency in hard_blocked:
                blocked_deps.append(f"{task.task_id}->{dependency}")

    if unknown:
        return _failed(
            PlanCheckName.dependencies_sound,
            PlanReasonCode.DEPENDENCY_UNKNOWN,
            f"dependency on a task not in this plan: {', '.join(unknown)}",
            unknown,
        )

    cycle = _find_cycle(tasks)
    if cycle:
        return _failed(
            PlanCheckName.dependencies_sound,
            PlanReasonCode.DEPENDENCY_CYCLE,
            f"dependency cycle: {' -> '.join(cycle)}",
            cycle,
        )

    if blocked_deps:
        return _failed(
            PlanCheckName.dependencies_sound,
            PlanReasonCode.DEPENDENCY_BLOCKED,
            f"task depends on a task blocked on evidence or conflict: {', '.join(blocked_deps)}",
            blocked_deps,
        )

    return _passed(PlanCheckName.dependencies_sound)


def _find_cycle(tasks: list[TaskOutcome]) -> list[str]:
    """Return one cycle as a path, or [] if the graph is acyclic. Deterministic."""
    edges = {task.task_id: list(task.depends_on) for task in tasks}
    visiting: set[str] = set()
    done: set[str] = set()
    path: list[str] = []

    def walk(node: str) -> list[str]:
        if node in done:
            return []
        if node in visiting:
            start = path.index(node) if node in path else 0
            return [*path[start:], node]

        visiting.add(node)
        path.append(node)
        for dependency in edges.get(node, []):
            found = walk(dependency)
            if found:
                return found
        path.pop()
        visiting.discard(node)
        done.add(node)
        return []

    for task in tasks:
        found = walk(task.task_id)
        if found:
            return found
    return []


# --------------------------------------------------------------------------- 3. consistency


def plan_consistent(*, tasks: list[TaskOutcome], config: PlanConfig) -> PlanCheckResult:
    """FAIL when two tasks inside the plan contradict each other.

    Two kinds, both generic:

      * the same action type twice against the same target — a double commitment the action gate
        cannot see, because each task looks fine on its own and neither is yet executed
      * two action types the config declares mutually exclusive against one target, such as
        rebooking a passenger and also arranging ground transport for them
    """
    seen: dict[tuple[str, str], str] = {}
    duplicates: list[str] = []
    for task in tasks:
        for ref in task.target_refs:
            key = (task.action_type, ref)
            if key in seen:
                duplicates.append(f"{task.action_type}:{ref} ({seen[key]}, {task.task_id})")
            else:
                seen[key] = task.task_id

    if duplicates:
        return _failed(
            PlanCheckName.plan_consistent,
            PlanReasonCode.DUPLICATE_TASK,
            f"the same action targets one entity twice: {', '.join(duplicates)}",
            duplicates,
        )

    exclusive = config.exclusive_pairs()
    if exclusive:
        by_ref: dict[str, set[str]] = {}
        for task in tasks:
            for ref in task.target_refs:
                by_ref.setdefault(ref, set()).add(task.action_type)

        clashes = [
            f"{ref}: {' + '.join(sorted(pair))}"
            for ref, actions in sorted(by_ref.items())
            for pair in exclusive
            if pair <= actions
        ]
        if clashes:
            return _failed(
                PlanCheckName.plan_consistent,
                PlanReasonCode.MUTUALLY_EXCLUSIVE_TASKS,
                f"mutually exclusive actions target one entity: {', '.join(clashes)}",
                clashes,
            )

    return _passed(PlanCheckName.plan_consistent)


# ------------------------------------------------------------------------------ 4. coverage


def coverage_complete(
    *, tasks: list[TaskOutcome], coverage: CoverageDeclaration
) -> PlanCheckResult:
    """FAIL when the plan does not account for the declared impacted set.

    An undeclared coverage set is a FAIL, not an assumption of full coverage. A plan that quietly
    addresses five of eight flights is the failure mode this exists for, and it is invisible at
    action level because every one of the five actions is perfectly good.

    An entity may be left unaddressed only with a stated reason.
    """
    if not coverage.declared:
        return _failed(
            PlanCheckName.coverage_complete,
            PlanReasonCode.COVERAGE_NOT_DECLARED,
            "no impacted set was declared, so coverage cannot be established",
        )

    addressed = {ref for task in tasks for ref in task.target_refs}
    uncovered = [
        ref
        for ref in coverage.impacted_refs
        if ref not in addressed and ref not in coverage.deferred
    ]

    if uncovered:
        return _failed(
            PlanCheckName.coverage_complete,
            PlanReasonCode.COVERAGE_INCOMPLETE,
            f"{len(uncovered)} impacted entity/entities neither addressed nor deferred with a "
            f"reason: {', '.join(uncovered)}",
            uncovered,
        )

    unexplained = sorted(
        ref for ref, reason in coverage.deferred.items() if not str(reason).strip()
    )
    if unexplained:
        return _failed(
            PlanCheckName.coverage_complete,
            PlanReasonCode.COVERAGE_INCOMPLETE,
            f"deferred without a reason: {', '.join(unexplained)}",
            unexplained,
        )

    return _passed(PlanCheckName.coverage_complete)


# ------------------------------------------------------------------------------ 5. exposure


def exposure_within_limits(
    *, tasks: list[TaskOutcome], exposure: ExposureInputs, config: PlanConfig
) -> PlanCheckResult:
    """FAIL when the aggregate exceeds a configured budget, or cannot be established.

    An unknown figure is a breach, not a zero. A plan whose cost nobody has established is not a
    plan that costs nothing, and reporting it as within budget would be the most expensive
    rounding error in the system.
    """
    limits = config.limits

    if exposure.unresolved_cohorts:
        return _failed(
            PlanCheckName.exposure_within_limits,
            PlanReasonCode.EXPOSURE_UNKNOWN,
            f"{len(exposure.unresolved_cohorts)} entitlement cohort(s) unresolved, so the plan's "
            f"exposure is unknown: {', '.join(exposure.unresolved_cohorts)}",
            list(exposure.unresolved_cohorts),
        )

    if len(tasks) > limits.max_tasks:
        return _failed(
            PlanCheckName.exposure_within_limits,
            PlanReasonCode.PLAN_TOO_LARGE,
            f"{len(tasks)} tasks exceeds the configured maximum of {limits.max_tasks}",
        )

    unknown = [
        field
        for field, value in (
            ("total_exposure_inr", exposure.total_exposure_inr),
            ("passengers_affected", exposure.passengers_affected),
            ("rooms_committed", exposure.rooms_committed),
            ("external_effects", exposure.external_effects),
        )
        if value is None
    ]
    if unknown:
        return _failed(
            PlanCheckName.exposure_within_limits,
            PlanReasonCode.EXPOSURE_UNKNOWN,
            f"not established, so treated as a breach rather than as zero: {', '.join(unknown)}",
            unknown,
        )

    high_risk_actions = sum(1 for task in tasks if task.risk_tier is RiskTier.high)
    breaches = [
        f"{name} {value} > {limit}"
        for name, value, limit in (
            ("total_exposure_inr", exposure.total_exposure_inr, limits.max_total_exposure_inr),
            ("passengers_affected", exposure.passengers_affected, limits.max_passengers_affected),
            ("rooms_committed", exposure.rooms_committed, limits.max_rooms_committed),
            ("external_effects", exposure.external_effects, limits.max_external_effects),
            ("high_risk_actions", high_risk_actions, limits.max_high_risk_actions),
        )
        if value is not None and value > limit
    ]

    if breaches:
        return _failed(
            PlanCheckName.exposure_within_limits,
            PlanReasonCode.EXPOSURE_LIMIT_BREACHED,
            f"plan exceeds configured limits: {'; '.join(breaches)}",
            breaches,
        )

    return _passed(PlanCheckName.exposure_within_limits)


# ----------------------------------------------------------------------------- 6. plan risk


def plan_risk(
    *, tasks: list[TaskOutcome], exposure: ExposureInputs, config: PlanConfig
) -> PlanCheckResult:
    """Classify the plan's risk. NEVER FAILs; a high tier blocks through aggregation.

    The tier is the highest tier among the tasks, escalated to `high` when the aggregate crosses a
    configured fraction of a budget or a count. That escalation is the point of the whole plan
    level: forty individually-medium actions can be a high-risk plan, and nothing at action level
    can say so.
    """
    if not tasks:
        return _passed(PlanCheckName.plan_risk, tier=RiskTier.high)

    tier = RiskTier.low
    if any(task.risk_tier is RiskTier.high for task in tasks):
        tier = RiskTier.high
    elif any(task.risk_tier is RiskTier.medium for task in tasks):
        tier = RiskTier.medium

    limits = config.limits
    escalation = config.escalation
    high_risk_actions = sum(1 for task in tasks if task.risk_tier is RiskTier.high)
    external_effects = exposure.external_effects or 0

    reasons: list[str] = []
    if (
        exposure.total_exposure_inr is not None
        and exposure.total_exposure_inr
        >= limits.max_total_exposure_inr * escalation.exposure_fraction
    ):
        reasons.append(
            f"exposure {exposure.total_exposure_inr} is at or above "
            f"{escalation.exposure_fraction:g} of the {limits.max_total_exposure_inr} budget"
        )
    if (
        exposure.passengers_affected is not None
        and exposure.passengers_affected
        >= limits.max_passengers_affected * escalation.passengers_fraction
    ):
        reasons.append(
            f"{exposure.passengers_affected} passengers is at or above "
            f"{escalation.passengers_fraction:g} of the {limits.max_passengers_affected} limit"
        )
    if high_risk_actions >= escalation.high_risk_action_count:
        reasons.append(f"{high_risk_actions} high-risk actions in one plan")
    if external_effects >= escalation.external_effect_count:
        reasons.append(f"{external_effects} external effects in one plan")

    # An unknown exposure is not evidence of a small plan.
    if exposure.total_exposure_inr is None or exposure.unresolved_cohorts:
        reasons.append("aggregate exposure is not established")

    if reasons:
        return PlanCheckResult(
            name=PlanCheckName.plan_risk,
            state=CheckState.passed,
            reason_code=PlanReasonCode.HUMAN_APPROVAL_REQUIRED,
            reason="; ".join(reasons),
            tier=RiskTier.high,
        )

    if tier is RiskTier.high:
        return PlanCheckResult(
            name=PlanCheckName.plan_risk,
            state=CheckState.passed,
            reason_code=PlanReasonCode.HUMAN_APPROVAL_REQUIRED,
            reason=f"{high_risk_actions} high-risk action(s) in the plan",
            tier=RiskTier.high,
        )

    return _passed(PlanCheckName.plan_risk, tier=tier)
