"""Candidate recovery-plan evaluation — STREAM B, new in Phase 2.

Several candidate plans arrive; each is put through plan-level assurance and reported on. What comes
back says which are **admissible** and gives the arithmetic needed to compare them.

**The boundary this module holds: B decides admissibility, never preference.** There is no score, no
ranking and no `recommended` flag anywhere in the contract, and a test asserts their absence. A
safety layer that also picks the winner stops being auditable — the operator would be approving the
gate's opinion rather than their own decision. Selection is the orchestrator's, informed by
`comparison`, which is plain arithmetic over results already computed.

Comparison runs against **recorded facts only**, through `app.assurance.whatif`. If the zero-write
guard refuses, no comparison happens at all.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.assurance.checks import dedupe
from app.assurance.plan_contract import (
    CoverageDeclaration,
    ExposureInputs,
    PlanAssuranceResult,
    PlanConfig,
    PlanUnderReview,
)
from app.assurance.plan_gate import evaluate_plan
from app.assurance.whatif import WhatIfPolicy, WhatIfRequest, WhatIfVerdict, assert_zero_write
from app.models.enums import RiskTier


class CandidateInput(BaseModel):
    """One candidate, with the recorded facts it should be assessed against."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    plan: PlanUnderReview
    coverage: CoverageDeclaration
    exposure: ExposureInputs


class CandidateComparison(BaseModel):
    """Arithmetic over an already-computed result. Deliberately contains no preference.

    Every field is a count or a sum a reviewer can re-derive by hand from the plan and its
    evaluation. Nothing here is weighted, normalised or combined into an index, because the moment
    it is, this module is choosing.
    """

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    plan_hash: str
    admissible: bool
    decision: str
    plan_risk_tier: RiskTier

    task_count: int
    exposure_inr: int | None
    passengers_affected: int | None
    rooms_committed: int | None
    external_effects: int | None

    high_risk_actions: int
    approvals_required: int
    uncovered_entities: int
    blocking_checks: list[str] = Field(default_factory=list)
    unresolved_cohorts: list[str] = Field(default_factory=list)


class CandidateSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluations: list[PlanAssuranceResult] = Field(default_factory=list)
    comparison: list[CandidateComparison] = Field(default_factory=list)
    #: Candidate ids whose plan-level assurance admits them. Order follows input order, never rank.
    admissible: list[str] = Field(default_factory=list)
    #: 'proceed' when at least one candidate is admissible, otherwise 'needs_human'.
    decision: str = "needs_human"
    blocking_reasons: list[str] = Field(default_factory=list)
    what_if: WhatIfVerdict | None = None

    @property
    def requires_human(self) -> bool:
        return self.decision != "proceed"


def _comparison_for(
    *, candidate: CandidateInput, result: PlanAssuranceResult, uncovered: int
) -> CandidateComparison:
    return CandidateComparison(
        candidate_id=candidate.candidate_id,
        plan_hash=result.plan_hash,
        admissible=result.admissible,
        decision=result.decision.value,
        plan_risk_tier=result.plan_risk_tier,
        task_count=len(candidate.plan.tasks),
        exposure_inr=candidate.exposure.total_exposure_inr,
        passengers_affected=candidate.exposure.passengers_affected,
        rooms_committed=candidate.exposure.rooms_committed,
        external_effects=candidate.exposure.external_effects,
        high_risk_actions=sum(
            1 for task in candidate.plan.tasks if task.risk_tier is RiskTier.high
        ),
        approvals_required=sum(1 for task in candidate.plan.tasks if task.needs_human),
        uncovered_entities=uncovered,
        blocking_checks=[name.value for name in result.blocking],
        unresolved_cohorts=list(candidate.exposure.unresolved_cohorts),
    )


def _uncovered_count(candidate: CandidateInput) -> int:
    coverage = candidate.coverage
    if not coverage.declared:
        return len(coverage.impacted_refs)
    addressed = {ref for task in candidate.plan.tasks for ref in task.target_refs}
    return sum(
        1 for ref in coverage.impacted_refs if ref not in addressed and ref not in coverage.deferred
    )


def evaluate_candidates(
    *,
    candidates: list[CandidateInput],
    config: PlanConfig | None,
    config_version: str,
    config_hash: str,
    what_if_policy: WhatIfPolicy,
    seed: int | None = None,
    provider_modes: dict[str, str] | None = None,
    real_dispatch_enabled: bool = False,
) -> CandidateSet:
    """Evaluate every candidate and report which are admissible.

    Fail-closed in three ways:

      * the zero-write guard runs FIRST. If it refuses, nothing is evaluated and no candidate is
        admissible — a comparison that could touch something real is not run at all.
      * an unevaluable candidate is inadmissible, not skipped. If evaluating one raises, the whole
        set returns needs_human: a partially evaluated candidate set is not a choice.
      * zero admissible candidates is needs_human, never the closest match.
    """
    verdict = assert_zero_write(
        request=WhatIfRequest(
            candidate_count=len(candidates),
            seed=seed,
            provider_modes=provider_modes or {},
            real_dispatch_enabled=real_dispatch_enabled,
        ),
        policy=what_if_policy,
    )

    if not verdict.permitted:
        return CandidateSet(
            decision="needs_human",
            blocking_reasons=[refusal.value for refusal in verdict.refusals],
            what_if=verdict,
        )

    evaluations: list[PlanAssuranceResult] = []
    comparison: list[CandidateComparison] = []
    admissible: list[str] = []
    blocking: list[str] = []

    for candidate in candidates:
        try:
            result = evaluate_plan(
                plan=candidate.plan,
                coverage=candidate.coverage,
                exposure=candidate.exposure,
                config=config,
                config_version=config_version,
                config_hash=config_hash,
            )
        except Exception as exc:
            # One unevaluable candidate makes the whole set undecidable. Selecting from the
            # remainder would be choosing without knowing what was excluded.
            return CandidateSet(
                decision="needs_human",
                blocking_reasons=[
                    f"CANDIDATE_EVALUATION_FAILED:{candidate.candidate_id}:{type(exc).__name__}"
                ],
                what_if=verdict,
            )

        evaluations.append(result)
        comparison.append(
            _comparison_for(
                candidate=candidate, result=result, uncovered=_uncovered_count(candidate)
            )
        )
        if result.admissible:
            admissible.append(candidate.candidate_id)
        else:
            blocking.extend(name.value for name in result.blocking)

    return CandidateSet(
        evaluations=evaluations,
        comparison=comparison,
        admissible=admissible,
        decision="proceed" if admissible else "needs_human",
        blocking_reasons=[] if admissible else dedupe(blocking),
        what_if=verdict,
    )
