"""What a human approval may and may not release.

Three rules under test, in order of how much damage their absence would do:

  1. approval covers risk, never failed evidence or an unresolved conflict
  2. a plan approval releases the plan-level risk block and nothing else
  3. high-risk actions are always approved separately
"""

from __future__ import annotations

import pytest

from app.assurance.approval import (
    ApprovalRefusal,
    ApprovalScope,
    ApprovalScopeKind,
    approval_summary,
    may_approve_action,
    may_approve_plan,
    plan_approval_covers,
    tasks_still_needing_own_decision,
)
from app.assurance.contract import AssuranceResult, CheckName, CheckResult, ReasonCode
from app.assurance.plan_contract import (
    PlanApprovalPolicy,
    PlanAssuranceResult,
    PlanCheckName,
    PlanCheckResult,
    TaskOutcome,
)
from app.models.enums import AssuranceDecision, CheckState, RiskTier

POLICY = PlanApprovalPolicy(
    covers_tiers=[RiskTier.low, RiskTier.medium],
    high_risk_always_separate=True,
    bound_to_plan_hash=True,
)
PLAN_HASH = "abc123def456"


def _action_result(
    *checks: CheckResult, decision=AssuranceDecision.needs_human, tier=RiskTier.medium
) -> AssuranceResult:
    return AssuranceResult(
        decision=decision,
        risk_tier=tier,
        checks=list(checks),
        config_version="assurance-v2",
        config_hash="deadbeef",
    )


def _risk_only(tier: RiskTier = RiskTier.high) -> AssuranceResult:
    return _action_result(
        CheckResult(
            name=CheckName.action_risk,
            state=CheckState.passed,
            reason_code=ReasonCode.HUMAN_APPROVAL_REQUIRED,
            tier=tier,
        ),
        tier=tier,
    )


def _evidence_blocked() -> AssuranceResult:
    return _action_result(
        CheckResult(
            name=CheckName.evidence_complete,
            state=CheckState.failed,
            reason_code=ReasonCode.MISSING_REQUIRED_FACT,
            reason="cause_evidence.unavoidable_despite_reasonable_measures is absent",
        )
    )


def _conflict_blocked() -> AssuranceResult:
    return _action_result(
        CheckResult(
            name=CheckName.no_conflicts,
            state=CheckState.failed,
            reason_code=ReasonCode.DUPLICATE_ACTION,
        )
    )


def _plan_result(
    *, blocking: list[PlanCheckName], decision=AssuranceDecision.needs_human
) -> PlanAssuranceResult:
    return PlanAssuranceResult(
        decision=decision,
        plan_risk_tier=RiskTier.high,
        checks=[
            PlanCheckResult(
                name=name,
                state=CheckState.failed if name in blocking else CheckState.passed,
            )
            for name in PlanCheckName
        ],
        blocking=blocking,
        group_reference="GRP-1",
        plan_hash=PLAN_HASH,
        config_version="assurance-v2",
        config_hash="deadbeef",
    )


def _task(
    task_id: str = "t1",
    *,
    action: str = "reserve_hotel_block",
    tier: RiskTier = RiskTier.medium,
    kinds: list[str] | None = None,
    decision: AssuranceDecision = AssuranceDecision.needs_human,
) -> TaskOutcome:
    return TaskOutcome(
        task_id=task_id,
        action_type=action,
        decision=decision,
        risk_tier=tier,
        blocking_kinds=kinds if kinds is not None else ["risk"],
        approvable=(kinds or ["risk"]) == ["risk"],
    )


# ---------------------------------------------------- rule 1: risk, never failed evidence


class TestApprovalCoversRiskOnly:
    def test_a_risk_only_block_may_be_approved(self):
        assert may_approve_action(_risk_only()).permitted

    def test_a_missing_fact_may_not_be_approved(self):
        """The single most important refusal in the system."""
        check = may_approve_action(_evidence_blocked())
        assert not check.permitted
        assert check.refusal is ApprovalRefusal.NOT_APPROVABLE_EVIDENCE
        assert check.unresolved == ["MISSING_REQUIRED_FACT"]

    def test_a_conflict_may_not_be_approved(self):
        """A double-booked room is resolved, not waved through."""
        check = may_approve_action(_conflict_blocked())
        assert not check.permitted
        assert check.refusal is ApprovalRefusal.NOT_APPROVABLE_CONFLICT

    def test_risk_plus_evidence_may_not_be_approved(self):
        result = _action_result(
            CheckResult(
                name=CheckName.evidence_complete,
                state=CheckState.failed,
                reason_code=ReasonCode.MISSING_REQUIRED_FACT,
            ),
            CheckResult(
                name=CheckName.action_risk,
                state=CheckState.passed,
                reason_code=ReasonCode.HUMAN_APPROVAL_REQUIRED,
                tier=RiskTier.high,
            ),
        )
        assert not may_approve_action(result).permitted

    def test_there_is_nothing_to_approve_on_an_executable_result(self):
        executable = _action_result(
            CheckResult(name=CheckName.action_risk, state=CheckState.passed),
            decision=AssuranceDecision.execute,
        )
        check = may_approve_action(executable)
        assert not check.permitted
        assert check.refusal is ApprovalRefusal.NOTHING_TO_APPROVE

    def test_a_gate_that_could_not_run_may_not_be_approved(self):
        unavailable = _action_result(
            *[
                CheckResult(
                    name=name, state=CheckState.failed, reason_code=ReasonCode.CONFIG_MISSING
                )
                for name in CheckName
            ]
        )
        assert not may_approve_action(unavailable).permitted


class TestPlanApprovalIsRiskOnlyToo:
    def test_a_plan_blocked_only_on_risk_may_be_approved(self):
        assert may_approve_plan(_plan_result(blocking=[PlanCheckName.plan_risk])).permitted

    @pytest.mark.parametrize(
        "check",
        [
            PlanCheckName.coverage_complete,
            PlanCheckName.exposure_within_limits,
            PlanCheckName.dependencies_sound,
            PlanCheckName.plan_consistent,
            PlanCheckName.tasks_authorised,
        ],
    )
    def test_any_other_blocking_check_is_not_approvable(self, check: PlanCheckName):
        """These describe a plan that cannot run. A decision does not cure them."""
        result = may_approve_plan(_plan_result(blocking=[check, PlanCheckName.plan_risk]))
        assert not result.permitted
        assert result.refusal is ApprovalRefusal.NOT_APPROVABLE_EVIDENCE

    def test_an_admissible_plan_has_nothing_to_approve(self):
        admissible = _plan_result(blocking=[], decision=AssuranceDecision.execute)
        assert may_approve_plan(admissible).refusal is ApprovalRefusal.NOTHING_TO_APPROVE


# ---------------------------------------------- rules 2 and 3: what a plan approval reaches


def _plan_approval(**overrides) -> ApprovalScope:
    payload = {
        "scope": ApprovalScopeKind.plan,
        "actor_id": "ops-42",
        "reason": "accepted the aggregate exposure for the BLR cascade",
        "plan_hash": PLAN_HASH,
        "covers_tiers": [RiskTier.low, RiskTier.medium],
    }
    payload.update(overrides)
    return ApprovalScope(**payload)


class TestPlanApprovalScope:
    def test_it_covers_a_medium_task(self):
        check = plan_approval_covers(
            approval=_plan_approval(),
            task=_task(tier=RiskTier.medium),
            plan_hash=PLAN_HASH,
            policy=POLICY,
        )
        assert check.permitted

    def test_it_covers_a_low_task(self):
        check = plan_approval_covers(
            approval=_plan_approval(),
            task=_task(tier=RiskTier.low),
            plan_hash=PLAN_HASH,
            policy=POLICY,
        )
        assert check.permitted

    def test_it_never_covers_a_high_risk_task(self):
        """Money, cancellation and bulk external effects get their own decision, every time."""
        check = plan_approval_covers(
            approval=_plan_approval(covers_tiers=[RiskTier.low, RiskTier.medium, RiskTier.high]),
            task=_task(action="notify_passengers", tier=RiskTier.high),
            plan_hash=PLAN_HASH,
            policy=POLICY,
        )
        assert not check.permitted
        assert check.refusal is ApprovalRefusal.HIGH_RISK_NEEDS_OWN_DECISION

    def test_editing_the_plan_voids_the_approval(self):
        check = plan_approval_covers(
            approval=_plan_approval(), task=_task(), plan_hash="a-different-hash", policy=POLICY
        )
        assert not check.permitted
        assert check.refusal is ApprovalRefusal.PLAN_HASH_MISMATCH

    def test_a_tier_outside_the_approval_is_not_covered(self):
        check = plan_approval_covers(
            approval=_plan_approval(covers_tiers=[RiskTier.low]),
            task=_task(tier=RiskTier.medium),
            plan_hash=PLAN_HASH,
            policy=POLICY,
        )
        assert check.refusal is ApprovalRefusal.TIER_NOT_COVERED

    def test_a_task_outside_an_explicit_task_list_is_not_covered(self):
        check = plan_approval_covers(
            approval=_plan_approval(covered_task_ids=["t9"]),
            task=_task("t1"),
            plan_hash=PLAN_HASH,
            policy=POLICY,
        )
        assert check.refusal is ApprovalRefusal.TASK_NOT_IN_SCOPE

    def test_an_action_scoped_approval_never_covers_a_plan_task(self):
        approval = ApprovalScope(
            scope=ApprovalScopeKind.action,
            actor_id="ops-42",
            reason="approved one action",
            evaluation_id=101,
        )
        check = plan_approval_covers(
            approval=approval, task=_task(), plan_hash=PLAN_HASH, policy=POLICY
        )
        assert not check.permitted

    def test_a_plan_approval_does_not_release_an_evidence_block(self):
        """Rule 1 applies at every level, whatever the tier."""
        check = plan_approval_covers(
            approval=_plan_approval(),
            task=_task(tier=RiskTier.medium, kinds=["evidence"]),
            plan_hash=PLAN_HASH,
            policy=POLICY,
        )
        assert not check.permitted
        assert check.refusal is ApprovalRefusal.NOT_APPROVABLE_EVIDENCE

    def test_high_risk_can_only_be_folded_in_by_changing_versioned_config(self):
        relaxed = POLICY.model_copy(update={"high_risk_always_separate": False})
        check = plan_approval_covers(
            approval=_plan_approval(covers_tiers=[RiskTier.high]),
            task=_task(tier=RiskTier.high),
            plan_hash=PLAN_HASH,
            policy=relaxed,
        )
        assert check.permitted, "possible, but only by publishing a config that says so"


class TestWhatIsStillOnTheOperatorsDesk:
    def test_high_risk_tasks_remain_after_a_plan_approval(self):
        tasks = [
            _task("hotel", tier=RiskTier.medium),
            _task("cash", action="evaluate_entitlements", tier=RiskTier.high),
            _task("notify", action="notify_passengers", tier=RiskTier.high),
        ]
        remaining = tasks_still_needing_own_decision(
            approval=_plan_approval(), tasks=tasks, plan_hash=PLAN_HASH, policy=POLICY
        )
        assert remaining == ["cash", "notify"]

    def test_an_executable_task_is_not_on_the_desk_at_all(self):
        tasks = [_task("ok", decision=AssuranceDecision.execute, kinds=[])]
        assert (
            tasks_still_needing_own_decision(
                approval=_plan_approval(), tasks=tasks, plan_hash=PLAN_HASH, policy=POLICY
            )
            == []
        )

    def test_a_voided_approval_leaves_everything_on_the_desk(self):
        tasks = [_task("a", tier=RiskTier.medium), _task("b", tier=RiskTier.low)]
        remaining = tasks_still_needing_own_decision(
            approval=_plan_approval(plan_hash="stale"),
            tasks=tasks,
            plan_hash=PLAN_HASH,
            policy=POLICY,
        )
        assert remaining == ["a", "b"]


class TestApprovalSummary:
    def test_it_tells_the_ui_to_hide_the_affordance(self):
        summary = approval_summary(_evidence_blocked())
        assert summary["approvable"] is False
        assert summary["refusal"] == ApprovalRefusal.NOT_APPROVABLE_EVIDENCE.value
        assert summary["unresolved"] == ["MISSING_REQUIRED_FACT"]

    def test_it_tells_the_ui_to_show_the_affordance(self):
        summary = approval_summary(_risk_only())
        assert summary["approvable"] is True
        assert summary["refusal"] is None
        assert summary["blocking_kinds"] == ["risk"]
