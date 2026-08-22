"""Plan-level assurance contracts — STREAM B, new in Phase 2.

A plan is group-scoped: one disruption group, many flights, many tasks. Six plan checks in a
fixed order, mirroring the action gate so the UI panel and the audit record can reuse one shape.

**What plan-level assurance is, precisely.** It answers the questions that are invisible when you
look at one action at a time — aggregate exposure, internal consistency, dependency integrity,
coverage of the impacted set. It is an ADMISSION gate.

**What it is not.** It does not authorise an action. Every task still passes the action gate at
execution time, because state moves between planning and execution. A plan approval releases the
PLAN-LEVEL risk block and nothing else; it never converts a task's `needs_human` into executable.
`authorises_no_action` is in the contract as a literal so no caller can misread the object and a
reviewer can find the boundary with one grep.

Nothing here changes `contract.py`. The action-level types are frozen and consumed by three other
streams.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AssuranceDecision, CheckState, RiskTier


class PlanCheckName(StrEnum):
    tasks_authorised = "tasks_authorised"
    dependencies_sound = "dependencies_sound"
    plan_consistent = "plan_consistent"
    coverage_complete = "coverage_complete"
    exposure_within_limits = "exposure_within_limits"
    plan_risk = "plan_risk"


#: Fixed order, so a UI panel and an audit record always agree on presentation.
PLAN_CHECK_ORDER: tuple[PlanCheckName, ...] = (
    PlanCheckName.tasks_authorised,
    PlanCheckName.dependencies_sound,
    PlanCheckName.plan_consistent,
    PlanCheckName.coverage_complete,
    PlanCheckName.exposure_within_limits,
    PlanCheckName.plan_risk,
)


class PlanReasonCode(StrEnum):
    """Stable codes. Separate from the action-level set: these are different failures."""

    OK = "OK"
    TASK_NOT_AUTHORISED = "TASK_NOT_AUTHORISED"
    TASK_EVALUATION_MISSING = "TASK_EVALUATION_MISSING"
    DEPENDENCY_CYCLE = "DEPENDENCY_CYCLE"
    DEPENDENCY_UNKNOWN = "DEPENDENCY_UNKNOWN"
    DEPENDENCY_BLOCKED = "DEPENDENCY_BLOCKED"
    DUPLICATE_TASK = "DUPLICATE_TASK"
    MUTUALLY_EXCLUSIVE_TASKS = "MUTUALLY_EXCLUSIVE_TASKS"
    COVERAGE_NOT_DECLARED = "COVERAGE_NOT_DECLARED"
    COVERAGE_INCOMPLETE = "COVERAGE_INCOMPLETE"
    EXPOSURE_UNKNOWN = "EXPOSURE_UNKNOWN"
    EXPOSURE_LIMIT_BREACHED = "EXPOSURE_LIMIT_BREACHED"
    PLAN_TOO_LARGE = "PLAN_TOO_LARGE"
    HUMAN_APPROVAL_REQUIRED = "HUMAN_APPROVAL_REQUIRED"
    PLAN_CONFIG_MISSING = "PLAN_CONFIG_MISSING"
    PLAN_EMPTY = "PLAN_EMPTY"


class PlanCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: PlanCheckName
    state: CheckState
    reason_code: PlanReasonCode = PlanReasonCode.OK
    reason: str | None = None
    #: Only `plan_risk` sets this. It may PASS while its tier still forces a human decision.
    tier: RiskTier | None = None
    #: Task ids or entity refs the finding concerns, so an operator can go straight to them.
    offending_refs: list[str] = Field(default_factory=list)

    @property
    def is_blocking(self) -> bool:
        return self.state is CheckState.failed


class TaskOutcome(BaseModel):
    """One task's action-level result, as the plan gate needs to see it.

    A projection rather than the `AssuranceResult` itself, so the plan gate stays a pure function
    over data the caller already has and does not reach into the database.
    """

    model_config = ConfigDict(extra="forbid")

    task_id: str
    action_type: str
    target_refs: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    decision: AssuranceDecision
    risk_tier: RiskTier
    #: From app.assurance.blocking.blocking_kinds. Empty when nothing blocked.
    blocking_kinds: list[str] = Field(default_factory=list)
    #: True only when the sole reason a human is needed is risk.
    approvable: bool = False
    evaluation_id: int | None = None

    @property
    def blocked_on_evidence_or_conflict(self) -> bool:
        """A block a human cannot approve away, so the plan cannot execute as a unit."""
        return any(kind in {"evidence", "conflict"} for kind in self.blocking_kinds)

    @property
    def needs_human(self) -> bool:
        return self.decision is AssuranceDecision.needs_human


class CoverageDeclaration(BaseModel):
    """What the cascade said was impacted, and what the plan chose not to address.

    Every entity ref here comes from Stream C's cascade rollup. Stream B never derives an
    impacted set — it only checks that the plan accounts for the one it was given.
    """

    model_config = ConfigDict(extra="forbid")

    #: Absent rather than empty when the caller never declared coverage. The distinction matters:
    #: an empty impacted set is a claim, a missing one is a gap.
    declared: bool = False
    impacted_refs: list[str] = Field(default_factory=list)
    #: ref -> reason. An entity may be left unaddressed only with a stated reason.
    deferred: dict[str, str] = Field(default_factory=dict)


class ExposureInputs(BaseModel):
    """Aggregate figures the plan would commit.

    Every value is supplied by the caller from Stream C's records. `None` means "not established",
    which the exposure check treats as a breach rather than as zero — a plan whose cost is unknown
    is not a plan whose cost is nothing.
    """

    model_config = ConfigDict(extra="forbid")

    total_exposure_inr: int | None = None
    passengers_affected: int | None = None
    rooms_committed: int | None = None
    external_effects: int | None = None
    #: Cohorts the entitlement engine could not resolve. Any entry makes exposure unknown.
    unresolved_cohorts: list[str] = Field(default_factory=list)


class PlanUnderReview(BaseModel):
    """The plan the gate is asked about, at group scope."""

    model_config = ConfigDict(extra="forbid")

    plan_id: int | None = None
    group_reference: str
    #: In `task_order`. Order is part of the plan's identity.
    tasks: list[TaskOutcome] = Field(default_factory=list)
    generator: str | None = None

    def hash(self) -> str:
        """Stable identity for the plan's shape.

        An approval is bound to this. Any change to the task set, their order, their targets or
        their dependencies produces a different hash, which voids the approval rather than
        migrating it.

        Target refs and dependencies are sorted so a caller reordering a list does not invent a
        new plan; task order is preserved because it is meaningful.
        """
        payload = {
            "group": self.group_reference,
            "tasks": [
                {
                    "task_id": task.task_id,
                    "action_type": task.action_type,
                    "target_refs": sorted(task.target_refs),
                    "depends_on": sorted(task.depends_on),
                }
                for task in self.tasks
            ],
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


class PlanAssuranceResult(BaseModel):
    """The immutable plan-level record. A corrected decision is a NEW evaluation."""

    model_config = ConfigDict(extra="forbid")

    decision: AssuranceDecision
    plan_risk_tier: RiskTier
    checks: list[PlanCheckResult]
    blocking: list[PlanCheckName] = Field(default_factory=list)

    group_reference: str
    plan_id: int | None = None
    plan_hash: str
    task_count: int = 0
    #: Action-level evaluation ids this plan was assessed against, for the audit trail.
    task_evaluation_ids: list[int] = Field(default_factory=list)
    exposure: dict[str, Any] = Field(default_factory=dict)

    config_version: str
    config_hash: str
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    #: Structural, not a comment. A plan result never authorises an action; every task passes the
    #: action gate at execution time. Grep for this to find the boundary.
    authorises_no_action: Literal[True] = True

    @property
    def admissible(self) -> bool:
        """The plan may proceed to per-action authorisation.

        Not the same as "may execute". Each task is still gated individually.
        """
        return self.decision in {
            AssuranceDecision.execute,
            AssuranceDecision.execute_flagged,
        }

    @property
    def requires_human(self) -> bool:
        return self.decision is AssuranceDecision.needs_human


class PlanLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_total_exposure_inr: int = 250000
    max_passengers_affected: int = 400
    max_rooms_committed: int = 80
    max_high_risk_actions: int = 6
    max_external_effects: int = 4
    max_tasks: int = 60


class PlanEscalation(BaseModel):
    """Where an aggregate of safe actions becomes a high-risk plan."""

    model_config = ConfigDict(extra="forbid")

    exposure_fraction: float = 0.6
    passengers_fraction: float = 0.6
    high_risk_action_count: int = 3
    external_effect_count: int = 2


class PlanApprovalPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    covers_tiers: list[RiskTier] = Field(default_factory=lambda: [RiskTier.low, RiskTier.medium])
    high_risk_always_separate: bool = True
    bound_to_plan_hash: bool = True


class WhatIfPolicy(BaseModel):
    """Bounded, zero-write, deterministic re-evaluation. Not a simulation engine."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    max_candidates: int = 4
    require_deterministic_seed: bool = True
    refuse_when_provider_live: bool = True
    figures_are_non_authoritative: bool = True


class PlanConfig(BaseModel):
    """Plan-level section of the versioned gate config."""

    model_config = ConfigDict(extra="forbid")

    limits: PlanLimits = Field(default_factory=PlanLimits)
    escalation: PlanEscalation = Field(default_factory=PlanEscalation)
    warn_allowed_checks: list[PlanCheckName] = Field(default_factory=list)
    mutually_exclusive_actions: list[list[str]] = Field(default_factory=list)
    approval: PlanApprovalPolicy = Field(default_factory=PlanApprovalPolicy)

    def warn_permitted(self, check: PlanCheckName) -> bool:
        return check in self.warn_allowed_checks

    def exclusive_pairs(self) -> set[frozenset[str]]:
        return {frozenset(pair) for pair in self.mutually_exclusive_actions if len(pair) == 2}


PLAN_CONFIG_UNAVAILABLE: Final = "unavailable"
