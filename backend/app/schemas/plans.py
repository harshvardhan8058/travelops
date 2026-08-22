"""Response contracts for the plan candidate, comparison and approval surface.

Owner: Stream A. Every rule these shapes report belongs to Stream B; nothing here re-derives a
decision.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PlanTaskOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    action_type: str
    task_order: int
    state: str
    target_refs: list[str] = Field(default_factory=list)
    depends_on: list[int] = Field(default_factory=list)


class CandidatePlanOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    incident_reference: str
    variant_key: str | None = None
    generator: str
    generated_at: datetime
    rationale: str | None = None
    selection_state: str
    selected_at: datetime | None = None
    selected_by: str | None = None
    plan_hash: str | None = None
    tasks: list[PlanTaskOut] = Field(default_factory=list)


class CandidatePlansResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_reference: str
    plans: list[CandidatePlanOut] = Field(default_factory=list)
    selected_plan_id: int | None = None


class CandidateComparisonRow(BaseModel):
    """One candidate's figures. Arithmetic only — there is deliberately no rank or score.

    Stream B provides no `recommended` flag and a test asserts its absence, because ranking
    recovery plans is a judgement with an owner, and the owner is a person.
    """

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    variant_key: str
    plan_id: int | None = None
    plan_hash: str
    admissible: bool
    decision: str
    plan_risk_tier: str
    task_count: int
    exposure_inr: int | None = None
    passengers_affected: int | None = None
    rooms_committed: int | None = None
    external_effects: int | None = None
    high_risk_actions: int = 0
    approvals_required: int = 0
    uncovered_entities: int = 0
    blocking_checks: list[str] = Field(default_factory=list)
    unresolved_cohorts: list[str] = Field(default_factory=list)
    selection_state: str | None = None
    rationale: str | None = None


class CandidateComparisonResponse(BaseModel):
    """Comparison over the same recorded facts — P2-D2.

    `basis` is a `Literal`, so the contract cannot express a projection. `not_a_forecast` is
    rendered verbatim by the console.
    """

    model_config = ConfigDict(extra="forbid")

    incident_reference: str
    basis: Literal["recorded_evidence"] = "recorded_evidence"
    not_a_forecast: str
    decision: str
    admissible: list[str] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    seed: int | None = None
    what_if: dict[str, Any] | None = None
    candidates: list[CandidateComparisonRow] = Field(default_factory=list)


class SelectPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=2000)


class PlanCheckOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    state: str
    reason_code: str
    reason: str | None = None
    tier: str | None = None
    offending_refs: list[str] = Field(default_factory=list)


class PlanTaskOutcomeOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    action_type: str
    decision: str
    risk_tier: str
    blocking_kinds: list[str] = Field(default_factory=list)
    approvable: bool
    evaluation_id: int | None = None
    target_refs: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)


class IncidentPlanAssuranceOut(BaseModel):
    """One member incident's row in the group matrix."""

    model_config = ConfigDict(extra="forbid")

    incident_reference: str
    plan_id: int
    variant_key: str | None = None
    task_count: int
    tasks: list[PlanTaskOutcomeOut] = Field(default_factory=list)
    awaiting_approval_count: int = 0
    config_version: str
    config_hash: str


class GroupAssuranceResponse(BaseModel):
    """Group-scoped plan assurance — P2-D1.

    **`authorises_no_action` is `Literal[True]`.** This response aggregates for display and
    grants nothing; every action still needs its own persisted evaluation and, where the gate
    demanded one, its own human decision. A reviewer can find the boundary by grepping for the
    field name.

    There is no aggregate assurance score at task, plan or group level. The gate is fail-closed
    and ordered — a mean of six checks would be a fiction — and a group is not "assured" because
    most of its incidents are.
    """

    model_config = ConfigDict(extra="forbid")

    group_reference: str
    decision: str
    plan_risk_tier: str
    task_count: int
    checks: list[PlanCheckOut] = Field(default_factory=list)
    blocking: list[str] = Field(default_factory=list)
    admissible: bool
    requires_human: bool
    authorises_no_action: Literal[True] = True
    plan_hash: str
    config_version: str
    config_hash: str
    #: True only when every member incident was judged under the same config hash. A group
    #: judged under two hashes is a fact a reviewer must see, not a detail to smooth over.
    config_hash_uniform: bool = True
    evaluated_at: datetime
    exposure: dict[str, Any] = Field(default_factory=dict)
    incidents: list[IncidentPlanAssuranceOut] = Field(default_factory=list)
    approval_preview: PlanApprovalPreview | None = None


class CoveredEvaluationOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation_id: int
    plan_task_id: int
    incident_reference: str
    action_type: str
    risk_tier: str
    human_decision_id: int


class ExcludedEvaluationOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation_id: int
    plan_task_id: int
    incident_reference: str
    action_type: str
    risk_tier: str
    reason_code: str
    reason: str


class PlanApprovalPreview(BaseModel):
    """What a plan approval would cover, before the operator commits.

    Both lists are returned. The excluded set is visible rather than hidden, so a reviewer sees
    the control was *unable* to cover something instead of assuming it chose not to.
    """

    model_config = ConfigDict(extra="forbid")

    plan_id: int | None = None
    plan_hash: str
    covered: list[CoveredEvaluationOut] = Field(default_factory=list)
    excluded: list[ExcludedEvaluationOut] = Field(default_factory=list)
    covered_count: int = 0
    excluded_count: int = 0
    refusal: str | None = None
    refusal_reason: str | None = None


class PlanApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=2000)


class PlanApprovalResponse(BaseModel):
    """The recorded approval and every decision it produced.

    `covered_count` always equals the number of `human_decision` rows actually written, because
    both come from the same partition. A caller can assert it, and a test does.
    """

    model_config = ConfigDict(extra="forbid")

    plan_approval_id: int | None = None
    plan_hash: str
    covered: list[CoveredEvaluationOut] = Field(default_factory=list)
    excluded: list[ExcludedEvaluationOut] = Field(default_factory=list)
    covered_count: int = 0
    excluded_count: int = 0
    refusal: str | None = None
    refusal_reason: str | None = None
    replayed: bool = False


GroupAssuranceResponse.model_rebuild()
