"""Assurance response contracts — STREAM A.

Matches `fixtures/api/assurance.json` (Stream C) and the `AssuranceEvaluation` type in
`frontend/src/api/types.ts` (Stream D).

The six check names and their fixed order are contractual: a UI panel and an audit record
must always agree on presentation, and a reviewer comparing the two must not have to
reconcile orderings. The order comes from `CHECK_ORDER` in `app/assurance/contract.py`
rather than being restated here.

`config_version` and `config_hash` appear on every evaluation, not just at the top level.
They are what makes a replay meaningful — an old evaluation has to stay interpretable
against the config it was actually made under, not today's.

Owner: Stream A (shape) / Stream B (the decisions it carries).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.assurance.contract import CheckName, ReasonCode
from app.models.enums import AssuranceDecision, CheckState, HumanDecisionType, RiskTier


class CheckResultOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: CheckName
    #: PASS | WARN | FAIL. Three states, deliberately: a WARN never collapses to a boolean.
    state: CheckState
    reason_code: ReasonCode
    reason: str | None = None
    #: Set on `action_risk`. The tier can force approval even when the check itself passed.
    tier: RiskTier | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class HumanDecisionOut(BaseModel):
    """An immutable operator response, keyed to exactly one evaluation."""

    model_config = ConfigDict(extra="forbid")

    id: int
    decision: HumanDecisionType
    #: Pseudonymous. No personal identity is stored or returned for a demo operator.
    actor_id: str
    reason: str
    decided_at: datetime


class AssuranceEvaluationOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    plan_task_id: int
    action_type: str
    decision: AssuranceDecision
    risk_tier: RiskTier
    evaluated_at: datetime
    checks: list[CheckResultOut] = Field(default_factory=list)
    blocking: list[CheckName] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    config_version: str
    config_hash: str
    #: Present only when a WARN was recorded. True restates what `execute_flagged` means:
    #: versioned config explicitly permitted that warning for that action. There is no
    #: global soft-failure bypass, so this is never true without such an entry.
    warn_permitted_by_config: bool | None = None
    human_decision: HumanDecisionOut | None = None


class AssuranceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_reference: str
    config_version: str
    config_hash: str
    evaluations: list[AssuranceEvaluationOut] = Field(default_factory=list)
    #: Evaluations that asked for a human and have not yet received one.
    awaiting_approval_count: int = 0


class DecisionRequest(BaseModel):
    """An operator approving or rejecting one evaluation."""

    model_config = ConfigDict(extra="forbid")

    decision: HumanDecisionType
    reason: str = Field(min_length=1, max_length=2000)
    #: Pseudonymous operator ID. Never a name or an email address.
    actor_id: str = Field(default="operator-1", max_length=64)


class DecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assurance_id: int
    decision: HumanDecisionType
    actor_id: str
    reason: str
    decided_at: datetime
    #: True when this evaluation already had a decision, so the original is returned
    #: unchanged. The gate record itself is never mutated by an operator response.
    replayed: bool = False
