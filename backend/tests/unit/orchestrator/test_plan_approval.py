"""Plan approval coverage — P2-D3, enforced rather than described.

The single most important behaviour in Phase 2: **a plan approval never covers a high-risk action,
and never covers an action blocked on evidence.** A bug here silently executes something a person
did not authorise, which is the worst failure this system can have.

The Bengaluru storm exercises the *refusal* path — every held action is a high-risk notification —
so the positive path is proved here, over a synthesised low-risk evaluation. A mechanism that is
only ever observed refusing is not known to work.

Owner: Stream A. Coverage rules: Stream B.
"""

from __future__ import annotations

import pytest

from app.assurance.approval import (
    ApprovalRefusal,
    ApprovalScope,
    ApprovalScopeKind,
    plan_approval_covers,
)
from app.assurance.contract import AssuranceResult, CheckName, CheckResult, ReasonCode
from app.assurance.plan_contract import PlanApprovalPolicy
from app.assurance.plan_gate import task_outcome_from
from app.models.enums import AssuranceDecision, CheckState, RiskTier

POLICY = PlanApprovalPolicy(
    covers_tiers=[RiskTier.low, RiskTier.medium],
    high_risk_always_separate=True,
    bound_to_plan_hash=True,
)
PLAN_HASH = "a" * 16


def _checks(*, failed: CheckName | None = None, high: bool = False) -> list[CheckResult]:
    results = []
    for name in (
        CheckName.evidence_complete,
        CheckName.sources_fresh,
        CheckName.entities_valid,
        CheckName.policy_compliant,
        CheckName.no_conflicts,
    ):
        if name is failed:
            results.append(
                CheckResult(
                    name=name,
                    state=CheckState.failed,
                    reason_code=ReasonCode.MISSING_EVIDENCE
                    if name is CheckName.evidence_complete
                    else ReasonCode.SOURCE_STALE,
                    reason="synthesised failure",
                )
            )
        else:
            results.append(CheckResult(name=name, state=CheckState.passed))
    results.append(
        CheckResult(
            name=CheckName.action_risk,
            state=CheckState.passed,
            reason_code=ReasonCode.HUMAN_APPROVAL_REQUIRED if high else ReasonCode.OK,
            tier=RiskTier.high if high else RiskTier.low,
        )
    )
    return results


def _result(*, tier: RiskTier, failed: CheckName | None = None) -> AssuranceResult:
    checks = _checks(failed=failed, high=tier is RiskTier.high)
    blocking = [CheckName.action_risk] if tier is RiskTier.high else []
    if failed is not None:
        blocking = [failed, *blocking]
    return AssuranceResult(
        decision=AssuranceDecision.needs_human,
        risk_tier=tier,
        checks=checks,
        blocking=blocking,
        config_version="assurance-v1-test",
        config_hash="0" * 16,
    )


def _outcome(*, tier: RiskTier, failed: CheckName | None = None, task_id: str = "1"):
    return task_outcome_from(
        task_id=task_id,
        action_type="check_connections" if tier is not RiskTier.high else "notify_passengers",
        result=_result(tier=tier, failed=failed),
        evaluation_id=int(task_id),
    )


def _scope(*, task_ids: list[str], plan_hash: str = PLAN_HASH) -> ApprovalScope:
    return ApprovalScope(
        scope=ApprovalScopeKind.plan,
        actor_id="operator-1",
        reason="network recovery approved",
        plan_hash=plan_hash,
        covers_tiers=list(POLICY.covers_tiers),
        covered_task_ids=task_ids,
    )


class TestTheRuleThatMatters:
    def test_a_high_risk_action_is_never_covered(self):
        """P2-D3's central rule. If this ever inverts, the demo is a liability."""
        task = _outcome(tier=RiskTier.high)
        check = plan_approval_covers(
            approval=_scope(task_ids=["1"]), task=task, plan_hash=PLAN_HASH, policy=POLICY
        )
        assert check.permitted is False
        assert check.refusal is ApprovalRefusal.HIGH_RISK_NEEDS_OWN_DECISION

    @pytest.mark.parametrize("tier", [RiskTier.low, RiskTier.medium, RiskTier.high])
    def test_a_failed_check_is_never_covered_at_any_tier(self, tier):
        """ "Low risk" is the tier most likely to tempt a shortcut, so every tier is asserted."""
        task = _outcome(tier=tier, failed=CheckName.evidence_complete)
        check = plan_approval_covers(
            approval=_scope(task_ids=["1"]), task=task, plan_hash=PLAN_HASH, policy=POLICY
        )
        assert check.permitted is False

    def test_a_stale_source_is_not_approvable_either(self):
        """P2-D3 names stale sources explicitly, alongside failed evidence."""
        task = _outcome(tier=RiskTier.low, failed=CheckName.sources_fresh)
        check = plan_approval_covers(
            approval=_scope(task_ids=["1"]), task=task, plan_hash=PLAN_HASH, policy=POLICY
        )
        assert check.permitted is False


class TestThePositivePath:
    """Proving the mechanism does something, not only that it refuses."""

    def test_a_low_risk_fully_evidenced_task_is_covered(self):
        task = _outcome(tier=RiskTier.low)
        check = plan_approval_covers(
            approval=_scope(task_ids=["1"]), task=task, plan_hash=PLAN_HASH, policy=POLICY
        )
        assert check.permitted is True, check.reason

    def test_a_medium_risk_fully_evidenced_task_is_covered(self):
        task = _outcome(tier=RiskTier.medium)
        check = plan_approval_covers(
            approval=_scope(task_ids=["1"]), task=task, plan_hash=PLAN_HASH, policy=POLICY
        )
        assert check.permitted is True, check.reason


class TestBinding:
    def test_a_re_planned_plan_voids_the_approval(self):
        """A different task set hashes differently, so the signature stops covering it.

        Without this, "approve the plan" silently grows to cover tasks nobody saw.
        """
        task = _outcome(tier=RiskTier.low)
        check = plan_approval_covers(
            approval=_scope(task_ids=["1"], plan_hash="b" * 16),
            task=task,
            plan_hash=PLAN_HASH,
            policy=POLICY,
        )
        assert check.permitted is False
        assert check.refusal is ApprovalRefusal.PLAN_HASH_MISMATCH

    def test_a_task_outside_the_declared_scope_is_not_covered(self):
        task = _outcome(tier=RiskTier.low, task_id="99")
        check = plan_approval_covers(
            approval=_scope(task_ids=["1", "2"]), task=task, plan_hash=PLAN_HASH, policy=POLICY
        )
        assert check.permitted is False
        assert check.refusal is ApprovalRefusal.TASK_NOT_IN_SCOPE

    def test_an_action_scoped_approval_covers_nothing_plan_wide(self):
        task = _outcome(tier=RiskTier.low)
        approval = _scope(task_ids=["1"]).model_copy(update={"scope": ApprovalScopeKind.action})
        check = plan_approval_covers(
            approval=approval, task=task, plan_hash=PLAN_HASH, policy=POLICY
        )
        assert check.permitted is False


class TestPolicySwitches:
    def test_turning_off_high_risk_separation_is_the_only_way_to_cover_high_risk(self):
        """Documents where the guarantee lives: config, not code.

        `plan_approval_tier` also has `CHECK (risk_tier IN ('low','medium'))`, so even with this
        switch flipped the database refuses to record a high tier on a plan approval.
        """
        relaxed = PlanApprovalPolicy(
            covers_tiers=[RiskTier.low, RiskTier.medium, RiskTier.high],
            high_risk_always_separate=True,
        )
        task = _outcome(tier=RiskTier.high)
        check = plan_approval_covers(
            approval=_scope(task_ids=["1"]), task=task, plan_hash=PLAN_HASH, policy=relaxed
        )
        assert check.permitted is False, "high_risk_always_separate must dominate covers_tiers"

    def test_a_tier_outside_the_declared_scope_is_refused(self):
        narrow = PlanApprovalPolicy(covers_tiers=[RiskTier.low])
        task = _outcome(tier=RiskTier.medium)
        approval = _scope(task_ids=["1"]).model_copy(update={"covers_tiers": [RiskTier.low]})
        check = plan_approval_covers(
            approval=approval, task=task, plan_hash=PLAN_HASH, policy=narrow
        )
        assert check.permitted is False
        assert check.refusal is ApprovalRefusal.TIER_NOT_COVERED
