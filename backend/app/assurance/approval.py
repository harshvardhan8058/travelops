"""What a human approval may and may not release — STREAM B, new in Phase 2.

Three rules, each enforced mechanically rather than described in a runbook.

**1. Approval covers risk, never failed evidence.** An action blocked because it is dangerous but
well-evidenced is approvable. An action blocked because a fact is missing, a source is stale, an
entity is unresolved or a constraint is breached is NOT approvable by anyone. The inputs must
change, which produces a new evaluation. Approval cannot manufacture a fact.

**2. A plan approval releases the plan-level risk block and nothing else.** It never converts a
task's `needs_human` into executable. Every task still passes the action gate at execution time.
This is the boundary that keeps plan-level assurance from becoming a second authorisation path.

**3. High-risk actions are always approved separately.** Money, cancellation and bulk external
effects get their own decision, with their own record and their own actor, every time. One click
must never authorise a bulk notification.

The practical consequence of rules 1 and 2 together: "plan approval covers low and medium actions"
means those actions need no *further* human involvement once the plan's aggregate risk is accepted
— not that their own gate results are overridden. A low-risk task blocked on a missing fact stays
blocked after the plan is approved.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from app.assurance.blocking import KIND_RISK, blocking_kinds, is_approvable
from app.assurance.contract import AssuranceResult
from app.assurance.plan_contract import (
    PlanApprovalPolicy,
    PlanAssuranceResult,
    TaskOutcome,
)
from app.models.enums import RiskTier


class ApprovalScopeKind(StrEnum):
    """Recorded explicitly so a flattened audit row can state its own scope."""

    action = "action"
    plan = "plan"


class ApprovalRefusal(StrEnum):
    """Why an approval was refused. Stable codes; the UI maps them to copy."""

    NOT_APPROVABLE_EVIDENCE = "NOT_APPROVABLE_EVIDENCE"
    NOT_APPROVABLE_CONFLICT = "NOT_APPROVABLE_CONFLICT"
    NOTHING_TO_APPROVE = "NOTHING_TO_APPROVE"
    PLAN_HASH_MISMATCH = "PLAN_HASH_MISMATCH"
    TIER_NOT_COVERED = "TIER_NOT_COVERED"
    HIGH_RISK_NEEDS_OWN_DECISION = "HIGH_RISK_NEEDS_OWN_DECISION"
    TASK_NOT_IN_SCOPE = "TASK_NOT_IN_SCOPE"


class ApprovalScope(BaseModel):
    """A recorded human approval and exactly what it reaches.

    `plan_hash` binds a plan approval to the plan's shape. Any change to the task set, its order,
    its targets or its dependencies produces a different hash, which VOIDS the approval rather
    than migrating it. An operator approved a specific plan, not the idea of a plan.
    """

    model_config = ConfigDict(extra="forbid")

    scope: ApprovalScopeKind
    actor_id: str
    reason: str
    granted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    #: Plan scope only.
    plan_hash: str | None = None
    covers_tiers: list[RiskTier] = Field(default_factory=list)
    #: Optional allow-list. When set, only these task ids are in scope.
    covered_task_ids: list[str] = Field(default_factory=list)

    #: Action scope only. The evaluation this approval was granted against.
    evaluation_id: int | None = None


class ApprovalCheck(BaseModel):
    """Whether an approval may be granted or applied, and why not."""

    model_config = ConfigDict(extra="forbid")

    permitted: bool
    refusal: ApprovalRefusal | None = None
    reason: str | None = None
    #: Reason codes an operator must resolve before approval becomes possible.
    unresolved: list[str] = Field(default_factory=list)


_PERMITTED: Final = ApprovalCheck(permitted=True)


def _refuse(refusal: ApprovalRefusal, reason: str, unresolved: list[str] | None = None):
    return ApprovalCheck(
        permitted=False, refusal=refusal, reason=reason, unresolved=unresolved or []
    )


# ------------------------------------------------------------------ may this be approved?


def may_approve_action(result: AssuranceResult) -> ApprovalCheck:
    """Whether an operator may approve this action-level result.

    Refuses anything blocked on evidence or conflict. This is the single most important refusal in
    the system: without it, an operator can click past a missing fact and the gate becomes theatre.
    """
    if not result.requires_human:
        return _refuse(
            ApprovalRefusal.NOTHING_TO_APPROVE,
            f"decision is {result.decision.value}; there is nothing awaiting a human",
        )

    kinds = blocking_kinds(result)
    if kinds == [KIND_RISK]:
        return _PERMITTED

    from app.assurance.blocking import KIND_CONFLICT, unapprovable_reasons

    refusal = (
        ApprovalRefusal.NOT_APPROVABLE_CONFLICT
        if KIND_CONFLICT in kinds
        else ApprovalRefusal.NOT_APPROVABLE_EVIDENCE
    )
    return _refuse(
        refusal,
        "approval covers risk, never failed evidence or an unresolved conflict; the inputs must "
        "change, which produces a new evaluation",
        unapprovable_reasons(result),
    )


def may_approve_plan(result: PlanAssuranceResult) -> ApprovalCheck:
    """Whether an operator may approve this plan's aggregate risk.

    A plan blocked by any check other than `plan_risk` is not approvable. Its checks describe a
    plan that cannot run — an inconsistent dependency graph, a coverage gap, an exposure figure
    nobody established — and none of those are cured by a decision.
    """
    from app.assurance.plan_contract import PlanCheckName

    if not result.requires_human:
        return _refuse(
            ApprovalRefusal.NOTHING_TO_APPROVE,
            f"decision is {result.decision.value}; there is nothing awaiting a human",
        )

    other = [name for name in result.blocking if name is not PlanCheckName.plan_risk]
    if other:
        from app.assurance.plan_gate import blocking_summary

        return _refuse(
            ApprovalRefusal.NOT_APPROVABLE_EVIDENCE,
            "the plan is blocked by checks a decision cannot cure: "
            f"{', '.join(name.value for name in other)}",
            blocking_summary(result),
        )

    return _PERMITTED


# ------------------------------------------------------- does an approval reach this task?


def plan_approval_covers(
    *,
    approval: ApprovalScope,
    task: TaskOutcome,
    plan_hash: str,
    policy: PlanApprovalPolicy,
) -> ApprovalCheck:
    """Whether a plan approval releases the need for a separate decision on this task.

    Even when it does, the task's own gate result still governs execution. This answers only
    "does this task need its own human decision as well".
    """
    if approval.scope is not ApprovalScopeKind.plan:
        return _refuse(
            ApprovalRefusal.TASK_NOT_IN_SCOPE, "this is an action-scoped approval, not a plan one"
        )

    if policy.bound_to_plan_hash and approval.plan_hash != plan_hash:
        return _refuse(
            ApprovalRefusal.PLAN_HASH_MISMATCH,
            f"approval was granted for plan {approval.plan_hash}, and this plan is {plan_hash}; "
            "the plan changed after it was approved",
        )

    if approval.covered_task_ids and task.task_id not in approval.covered_task_ids:
        return _refuse(
            ApprovalRefusal.TASK_NOT_IN_SCOPE,
            f"{task.task_id} is not in the approval's task list",
        )

    if task.risk_tier is RiskTier.high and policy.high_risk_always_separate:
        return _refuse(
            ApprovalRefusal.HIGH_RISK_NEEDS_OWN_DECISION,
            f"{task.action_type} is high risk: money, cancellation or a bulk external effect gets "
            "its own decision, every time",
        )

    if task.risk_tier not in approval.covers_tiers:
        return _refuse(
            ApprovalRefusal.TIER_NOT_COVERED,
            f"the approval covers {[t.value for t in approval.covers_tiers]} and this task is "
            f"{task.risk_tier.value}",
        )

    # A task blocked on evidence or conflict is never released by a plan approval, whatever its
    # tier. Rule 1 applies at every level.
    if task.blocked_on_evidence_or_conflict:
        return _refuse(
            ApprovalRefusal.NOT_APPROVABLE_EVIDENCE,
            f"{task.task_id} is blocked on {', '.join(task.blocking_kinds)}, which a plan approval "
            "cannot release",
            list(task.blocking_kinds),
        )

    return _PERMITTED


def tasks_still_needing_own_decision(
    *,
    approval: ApprovalScope,
    tasks: list[TaskOutcome],
    plan_hash: str,
    policy: PlanApprovalPolicy,
) -> list[str]:
    """Task ids that require their own human decision despite the plan approval.

    Drives the approval queue: after approving a plan, this is exactly what is still on the
    operator's desk.
    """
    return [
        task.task_id
        for task in tasks
        if task.needs_human
        and not plan_approval_covers(
            approval=approval, task=task, plan_hash=plan_hash, policy=policy
        ).permitted
    ]


def approval_summary(result: AssuranceResult) -> dict[str, object]:
    """Everything a UI needs to render the approval affordance honestly.

    When `approvable` is false the affordance should be absent rather than merely disabled: a
    greyed-out approve button invites someone to look for a way round it.
    """
    check = may_approve_action(result)
    return {
        "decision": result.decision.value,
        "requires_human": result.requires_human,
        "approvable": is_approvable(result),
        "blocking_kinds": blocking_kinds(result),
        "refusal": check.refusal.value if check.refusal else None,
        "unresolved": check.unresolved,
    }
