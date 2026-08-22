"""The six plan checks, in isolation.

Each check exists to catch something invisible one action at a time, so each test builds a plan
whose every individual task is perfectly good and asserts the plan is refused anyway.
"""

from __future__ import annotations

import pytest

from app.assurance.plan_checks import (
    coverage_complete,
    dependencies_sound,
    exposure_within_limits,
    plan_consistent,
    plan_risk,
    tasks_authorised,
)
from app.assurance.plan_contract import (
    CoverageDeclaration,
    ExposureInputs,
    PlanCheckName,
    PlanConfig,
    PlanReasonCode,
    TaskOutcome,
)
from app.models.enums import AssuranceDecision, CheckState, RiskTier

CONFIG = PlanConfig.model_validate(
    {
        "limits": {
            "max_total_exposure_inr": 250000,
            "max_passengers_affected": 400,
            "max_rooms_committed": 80,
            "max_high_risk_actions": 6,
            "max_external_effects": 4,
            "max_tasks": 60,
        },
        "escalation": {
            "exposure_fraction": 0.6,
            "passengers_fraction": 0.6,
            "high_risk_action_count": 3,
            "external_effect_count": 2,
        },
        "mutually_exclusive_actions": [["rebook_passengers", "arrange_ground_transport"]],
    }
)

SMALL_EXPOSURE = ExposureInputs(
    total_exposure_inr=10000, passengers_affected=20, rooms_committed=5, external_effects=0
)


def _task(
    task_id: str,
    *,
    action: str = "reserve_hotel_block",
    refs: list[str] | None = None,
    depends: list[str] | None = None,
    tier: RiskTier = RiskTier.medium,
    kinds: list[str] | None = None,
    decision: AssuranceDecision = AssuranceDecision.execute,
) -> TaskOutcome:
    return TaskOutcome(
        task_id=task_id,
        action_type=action,
        target_refs=refs if refs is not None else [f"flight:{task_id}"],
        depends_on=depends or [],
        decision=decision,
        risk_tier=tier,
        blocking_kinds=kinds or [],
        approvable=kinds == ["risk"],
    )


# --------------------------------------------------------------------- 1. tasks_authorised


class TestTasksAuthorised:
    def test_all_executable_passes(self):
        result = tasks_authorised(tasks=[_task("a"), _task("b")])
        assert result.state is CheckState.passed

    def test_a_risk_only_block_is_not_a_plan_failure(self):
        """Needing approval for risk is the normal path, handled at action level."""
        risky = _task(
            "a", tier=RiskTier.high, kinds=["risk"], decision=AssuranceDecision.needs_human
        )
        assert tasks_authorised(tasks=[risky]).state is CheckState.passed

    @pytest.mark.parametrize("kind", ["evidence", "conflict"])
    def test_a_block_approval_cannot_release_fails_the_plan(self, kind: str):
        blocked = _task("a", kinds=[kind], decision=AssuranceDecision.needs_human)
        result = tasks_authorised(tasks=[blocked, _task("b")])
        assert result.state is CheckState.failed
        assert result.reason_code is PlanReasonCode.TASK_NOT_AUTHORISED
        assert result.offending_refs == ["a"]

    def test_an_empty_plan_fails(self):
        result = tasks_authorised(tasks=[])
        assert result.state is CheckState.failed
        assert result.reason_code is PlanReasonCode.PLAN_EMPTY

    def test_needs_human_with_no_recorded_reason_is_treated_as_unevaluated(self):
        """A projection built without a real evaluation must not read as a risk-only block."""
        ghost = _task("a", kinds=[], decision=AssuranceDecision.needs_human)
        result = tasks_authorised(tasks=[ghost])
        assert result.state is CheckState.failed
        assert result.reason_code is PlanReasonCode.TASK_EVALUATION_MISSING


# ------------------------------------------------------------------ 2. dependencies_sound


class TestDependenciesSound:
    def test_a_linear_chain_passes(self):
        tasks = [_task("a"), _task("b", depends=["a"]), _task("c", depends=["b"])]
        assert dependencies_sound(tasks=tasks).state is CheckState.passed

    def test_no_dependencies_passes(self):
        assert dependencies_sound(tasks=[_task("a"), _task("b")]).state is CheckState.passed

    def test_a_dependency_outside_the_plan_fails(self):
        result = dependencies_sound(tasks=[_task("a", depends=["ghost"])])
        assert result.state is CheckState.failed
        assert result.reason_code is PlanReasonCode.DEPENDENCY_UNKNOWN

    def test_a_cycle_fails(self):
        tasks = [_task("a", depends=["b"]), _task("b", depends=["a"])]
        result = dependencies_sound(tasks=tasks)
        assert result.state is CheckState.failed
        assert result.reason_code is PlanReasonCode.DEPENDENCY_CYCLE

    def test_a_self_dependency_is_a_cycle(self):
        result = dependencies_sound(tasks=[_task("a", depends=["a"])])
        assert result.reason_code is PlanReasonCode.DEPENDENCY_CYCLE

    def test_a_three_node_cycle_fails(self):
        tasks = [
            _task("a", depends=["c"]),
            _task("b", depends=["a"]),
            _task("c", depends=["b"]),
        ]
        assert dependencies_sound(tasks=tasks).reason_code is PlanReasonCode.DEPENDENCY_CYCLE

    def test_depending_on_a_hard_blocked_task_fails(self):
        """A notification must not be sent when the rebooking it describes cannot happen."""
        tasks = [
            _task("rebook", kinds=["evidence"], decision=AssuranceDecision.needs_human),
            _task("notify", depends=["rebook"]),
        ]
        result = dependencies_sound(tasks=tasks)
        assert result.state is CheckState.failed
        assert result.reason_code is PlanReasonCode.DEPENDENCY_BLOCKED

    def test_depending_on_a_risk_blocked_task_is_fine(self):
        tasks = [
            _task(
                "cash", tier=RiskTier.high, kinds=["risk"], decision=AssuranceDecision.needs_human
            ),
            _task("notify", depends=["cash"]),
        ]
        assert dependencies_sound(tasks=tasks).state is CheckState.passed

    def test_a_diamond_is_not_a_cycle(self):
        tasks = [
            _task("a"),
            _task("b", depends=["a"]),
            _task("c", depends=["a"]),
            _task("d", depends=["b", "c"]),
        ]
        assert dependencies_sound(tasks=tasks).state is CheckState.passed


# ---------------------------------------------------------------------- 3. plan_consistent


class TestPlanConsistent:
    def test_distinct_targets_pass(self):
        tasks = [_task("a", refs=["flight:1"]), _task("b", refs=["flight:2"])]
        assert plan_consistent(tasks=tasks, config=CONFIG).state is CheckState.passed

    def test_the_same_action_twice_on_one_target_fails(self):
        """Neither task is executed yet, so no action-level conflict check can see this."""
        tasks = [
            _task("a", action="reserve_hotel_block", refs=["hotel:12"]),
            _task("b", action="reserve_hotel_block", refs=["hotel:12"]),
        ]
        result = plan_consistent(tasks=tasks, config=CONFIG)
        assert result.state is CheckState.failed
        assert result.reason_code is PlanReasonCode.DUPLICATE_TASK

    def test_mutually_exclusive_actions_on_one_target_fail(self):
        tasks = [
            _task("a", action="rebook_passengers", refs=["passenger:7"]),
            _task("b", action="arrange_ground_transport", refs=["passenger:7"]),
        ]
        result = plan_consistent(tasks=tasks, config=CONFIG)
        assert result.state is CheckState.failed
        assert result.reason_code is PlanReasonCode.MUTUALLY_EXCLUSIVE_TASKS

    def test_mutually_exclusive_actions_on_different_targets_pass(self):
        tasks = [
            _task("a", action="rebook_passengers", refs=["passenger:7"]),
            _task("b", action="arrange_ground_transport", refs=["passenger:8"]),
        ]
        assert plan_consistent(tasks=tasks, config=CONFIG).state is CheckState.passed

    def test_different_actions_on_one_target_pass_when_not_declared_exclusive(self):
        tasks = [
            _task("a", action="check_connections", refs=["flight:1"]),
            _task("b", action="assess_crew_impact", refs=["flight:1"]),
        ]
        assert plan_consistent(tasks=tasks, config=CONFIG).state is CheckState.passed

    def test_no_exclusive_pairs_configured_skips_that_rule(self):
        bare = PlanConfig()
        tasks = [
            _task("a", action="rebook_passengers", refs=["passenger:7"]),
            _task("b", action="arrange_ground_transport", refs=["passenger:7"]),
        ]
        assert plan_consistent(tasks=tasks, config=bare).state is CheckState.passed


# --------------------------------------------------------------------- 4. coverage_complete


class TestCoverageComplete:
    def test_full_coverage_passes(self):
        tasks = [_task("a", refs=["flight:1"]), _task("b", refs=["flight:2"])]
        coverage = CoverageDeclaration(declared=True, impacted_refs=["flight:1", "flight:2"])
        assert coverage_complete(tasks=tasks, coverage=coverage).state is CheckState.passed

    def test_an_undeclared_impacted_set_fails(self):
        """Absence of a declaration is not evidence of full coverage."""
        result = coverage_complete(tasks=[_task("a")], coverage=CoverageDeclaration())
        assert result.state is CheckState.failed
        assert result.reason_code is PlanReasonCode.COVERAGE_NOT_DECLARED

    def test_silently_dropping_a_flight_fails(self):
        """Every task is good; three of the eight flights are simply not mentioned."""
        tasks = [_task("a", refs=[f"flight:{n}"]) for n in range(1, 6)]
        coverage = CoverageDeclaration(
            declared=True, impacted_refs=[f"flight:{n}" for n in range(1, 9)]
        )
        result = coverage_complete(tasks=tasks, coverage=coverage)
        assert result.state is CheckState.failed
        assert result.reason_code is PlanReasonCode.COVERAGE_INCOMPLETE
        assert result.offending_refs == ["flight:6", "flight:7", "flight:8"]

    def test_an_explicit_deferral_with_a_reason_passes(self):
        tasks = [_task("a", refs=["flight:1"])]
        coverage = CoverageDeclaration(
            declared=True,
            impacted_refs=["flight:1", "flight:2"],
            deferred={"flight:2": "departs in 14 hours, handled by the next shift"},
        )
        assert coverage_complete(tasks=tasks, coverage=coverage).state is CheckState.passed

    @pytest.mark.parametrize("reason", ["", "   "])
    def test_a_deferral_without_a_reason_fails(self, reason: str):
        tasks = [_task("a", refs=["flight:1"])]
        coverage = CoverageDeclaration(
            declared=True, impacted_refs=["flight:1", "flight:2"], deferred={"flight:2": reason}
        )
        result = coverage_complete(tasks=tasks, coverage=coverage)
        assert result.state is CheckState.failed

    def test_a_declared_empty_impacted_set_passes(self):
        """An explicit claim that nothing is impacted is a claim, and it is allowed."""
        coverage = CoverageDeclaration(declared=True, impacted_refs=[])
        assert coverage_complete(tasks=[_task("a")], coverage=coverage).state is CheckState.passed


# ---------------------------------------------------------------- 5. exposure_within_limits


class TestExposureWithinLimits:
    def test_a_small_plan_passes(self):
        result = exposure_within_limits(tasks=[_task("a")], exposure=SMALL_EXPOSURE, config=CONFIG)
        assert result.state is CheckState.passed

    def test_forty_medium_actions_over_budget_fail(self):
        """Every action is individually fine. The aggregate is not."""
        tasks = [_task(str(n), refs=[f"hotel:{n}"]) for n in range(40)]
        exposure = ExposureInputs(
            total_exposure_inr=340000,
            passengers_affected=180,
            rooms_committed=62,
            external_effects=1,
        )
        result = exposure_within_limits(tasks=tasks, exposure=exposure, config=CONFIG)
        assert result.state is CheckState.failed
        assert result.reason_code is PlanReasonCode.EXPOSURE_LIMIT_BREACHED
        assert result.reason is not None and "total_exposure_inr 340000 > 250000" in result.reason

    @pytest.mark.parametrize(
        "field",
        ["total_exposure_inr", "passengers_affected", "rooms_committed", "external_effects"],
    )
    def test_an_unknown_figure_is_a_breach_not_a_zero(self, field: str):
        exposure = SMALL_EXPOSURE.model_copy(update={field: None})
        result = exposure_within_limits(tasks=[_task("a")], exposure=exposure, config=CONFIG)
        assert result.state is CheckState.failed
        assert result.reason_code is PlanReasonCode.EXPOSURE_UNKNOWN

    def test_an_unresolved_cohort_makes_exposure_unknown(self):
        exposure = SMALL_EXPOSURE.model_copy(update={"unresolved_cohorts": ["cohort-3"]})
        result = exposure_within_limits(tasks=[_task("a")], exposure=exposure, config=CONFIG)
        assert result.state is CheckState.failed
        assert result.reason_code is PlanReasonCode.EXPOSURE_UNKNOWN
        assert result.offending_refs == ["cohort-3"]

    def test_exactly_at_the_limit_passes(self):
        exposure = ExposureInputs(
            total_exposure_inr=250000,
            passengers_affected=400,
            rooms_committed=80,
            external_effects=4,
        )
        result = exposure_within_limits(tasks=[_task("a")], exposure=exposure, config=CONFIG)
        assert result.state is CheckState.passed

    def test_one_over_the_limit_fails(self):
        exposure = SMALL_EXPOSURE.model_copy(update={"total_exposure_inr": 250001})
        result = exposure_within_limits(tasks=[_task("a")], exposure=exposure, config=CONFIG)
        assert result.state is CheckState.failed

    def test_too_many_tasks_fails(self):
        tasks = [_task(str(n), refs=[f"flight:{n}"]) for n in range(61)]
        result = exposure_within_limits(tasks=tasks, exposure=SMALL_EXPOSURE, config=CONFIG)
        assert result.reason_code is PlanReasonCode.PLAN_TOO_LARGE

    def test_too_many_high_risk_actions_fails(self):
        tasks = [
            _task(
                str(n),
                refs=[f"flight:{n}"],
                tier=RiskTier.high,
                kinds=["risk"],
                decision=AssuranceDecision.needs_human,
            )
            for n in range(7)
        ]
        result = exposure_within_limits(tasks=tasks, exposure=SMALL_EXPOSURE, config=CONFIG)
        assert result.state is CheckState.failed
        assert result.reason is not None and "high_risk_actions" in result.reason


# ---------------------------------------------------------------------------- 6. plan_risk


class TestPlanRisk:
    def test_a_small_low_risk_plan_is_low(self):
        result = plan_risk(
            tasks=[_task("a", tier=RiskTier.low)], exposure=SMALL_EXPOSURE, config=CONFIG
        )
        assert result.state is CheckState.passed
        assert result.tier is RiskTier.low

    def test_the_highest_task_tier_governs(self):
        tasks = [_task("a", tier=RiskTier.low), _task("b", tier=RiskTier.medium)]
        assert (
            plan_risk(tasks=tasks, exposure=SMALL_EXPOSURE, config=CONFIG).tier is RiskTier.medium
        )

    def test_an_aggregate_of_medium_actions_can_be_a_high_risk_plan(self):
        """The reason the plan level exists. No single action would say this."""
        tasks = [_task(str(n), refs=[f"hotel:{n}"]) for n in range(20)]
        exposure = ExposureInputs(
            total_exposure_inr=160000,
            passengers_affected=100,
            rooms_committed=40,
            external_effects=0,
        )
        result = plan_risk(tasks=tasks, exposure=exposure, config=CONFIG)
        assert result.tier is RiskTier.high
        assert result.state is CheckState.passed, "classification succeeds; aggregation blocks"
        assert result.reason is not None and "0.6 of the 250000 budget" in result.reason

    def test_passenger_volume_escalates(self):
        exposure = SMALL_EXPOSURE.model_copy(update={"passengers_affected": 240})
        result = plan_risk(tasks=[_task("a")], exposure=exposure, config=CONFIG)
        assert result.tier is RiskTier.high

    def test_three_high_risk_actions_escalate(self):
        tasks = [
            _task(
                str(n),
                refs=[f"flight:{n}"],
                tier=RiskTier.high,
                kinds=["risk"],
                decision=AssuranceDecision.needs_human,
            )
            for n in range(3)
        ]
        result = plan_risk(tasks=tasks, exposure=SMALL_EXPOSURE, config=CONFIG)
        assert result.tier is RiskTier.high

    def test_external_effects_escalate(self):
        exposure = SMALL_EXPOSURE.model_copy(update={"external_effects": 2})
        assert plan_risk(tasks=[_task("a")], exposure=exposure, config=CONFIG).tier is RiskTier.high

    def test_unknown_exposure_is_not_evidence_of_a_small_plan(self):
        exposure = SMALL_EXPOSURE.model_copy(update={"total_exposure_inr": None})
        result = plan_risk(tasks=[_task("a")], exposure=exposure, config=CONFIG)
        assert result.tier is RiskTier.high
        assert result.reason is not None and "not established" in result.reason

    def test_this_check_never_fails(self):
        for exposure in (
            SMALL_EXPOSURE,
            ExposureInputs(),
            SMALL_EXPOSURE.model_copy(update={"unresolved_cohorts": ["c"]}),
        ):
            assert (
                plan_risk(tasks=[_task("a")], exposure=exposure, config=CONFIG).state
                is not CheckState.failed
            )

    def test_an_empty_plan_is_high_risk(self):
        result = plan_risk(tasks=[], exposure=SMALL_EXPOSURE, config=CONFIG)
        assert result.tier is RiskTier.high


def test_every_plan_check_name_is_produced_by_some_check():
    """A check nobody calls would leave a permanent FAIL in the record."""
    produced = {
        tasks_authorised(tasks=[_task("a")]).name,
        dependencies_sound(tasks=[_task("a")]).name,
        plan_consistent(tasks=[_task("a")], config=CONFIG).name,
        coverage_complete(tasks=[_task("a")], coverage=CoverageDeclaration(declared=True)).name,
        exposure_within_limits(tasks=[_task("a")], exposure=SMALL_EXPOSURE, config=CONFIG).name,
        plan_risk(tasks=[_task("a")], exposure=SMALL_EXPOSURE, config=CONFIG).name,
    }
    assert produced == set(PlanCheckName)
