"""Plan-level aggregation, config loading, and the plan entry point.

The aggregation order is the same contract as the action gate, one level up, so it is asserted rule
by rule. `TestAdmissionIsNotAuthorisation` is the boundary that keeps plan-level assurance from
becoming a second authorisation path.
"""

from __future__ import annotations

import pytest

from app.assurance.contract import AssuranceResult, CheckName, CheckResult, ReasonCode
from app.assurance.plan_contract import (
    PLAN_CHECK_ORDER,
    PLAN_CONFIG_UNAVAILABLE,
    CoverageDeclaration,
    ExposureInputs,
    PlanCheckName,
    PlanCheckResult,
    PlanReasonCode,
    PlanUnderReview,
    TaskOutcome,
)
from app.assurance.plan_gate import (
    aggregate_plan,
    blocking_summary,
    evaluate_plan,
    load_plan_config,
    task_outcome_from,
)
from app.errors import AssuranceConfigMissing
from app.models.enums import AssuranceDecision, CheckState, RiskTier

V2 = "./config/assurance.v2.yaml"
V1 = "./config/assurance.v1.yaml"


@pytest.fixture(scope="module")
def loaded():
    return load_plan_config(V2)


def _task(
    task_id: str,
    *,
    action: str = "reserve_hotel_block",
    refs: list[str] | None = None,
    tier: RiskTier = RiskTier.medium,
    kinds: list[str] | None = None,
    decision: AssuranceDecision = AssuranceDecision.execute,
    evaluation_id: int | None = None,
    depends: list[str] | None = None,
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
        evaluation_id=evaluation_id,
    )


def _plan(*tasks: TaskOutcome, group: str = "GRP-2026-0820-VOBL") -> PlanUnderReview:
    return PlanUnderReview(plan_id=1, group_reference=group, tasks=list(tasks))


SAFE_COVERAGE = CoverageDeclaration(declared=True, impacted_refs=["flight:a"])
SAFE_EXPOSURE = ExposureInputs(
    total_exposure_inr=1000, passengers_affected=10, rooms_committed=2, external_effects=0
)


def _check(name: PlanCheckName, state: CheckState = CheckState.passed) -> PlanCheckResult:
    tier = RiskTier.low if name is PlanCheckName.plan_risk else None
    return PlanCheckResult(name=name, state=state, tier=tier)


def _all_passing() -> list[PlanCheckResult]:
    return [_check(name) for name in PLAN_CHECK_ORDER]


class TestAggregationOrder:
    def test_missing_config_blocks_everything(
        self,
    ):
        result = aggregate_plan(
            checks=_all_passing(),
            plan=_plan(_task("a")),
            config=None,
            config_version="ignored",
            config_hash="ignored",
        )
        assert result.decision is AssuranceDecision.needs_human
        assert result.plan_risk_tier is RiskTier.high
        assert all(c.state is CheckState.failed for c in result.checks)
        assert result.blocking == list(PLAN_CHECK_ORDER)
        assert result.config_version == PLAN_CONFIG_UNAVAILABLE

    def test_any_failure_blocks(self, loaded):
        checks = _all_passing()
        checks[3] = _check(PlanCheckName.coverage_complete, CheckState.failed)
        result = aggregate_plan(
            checks=checks,
            plan=_plan(_task("a")),
            config=loaded.plan,
            config_version=loaded.version,
            config_hash=loaded.digest,
        )
        assert result.decision is AssuranceDecision.needs_human
        assert result.blocking == [PlanCheckName.coverage_complete]

    def test_a_high_risk_plan_blocks_even_when_every_check_passes(self, loaded):
        checks = _all_passing()
        checks[-1] = PlanCheckResult(
            name=PlanCheckName.plan_risk, state=CheckState.passed, tier=RiskTier.high
        )
        result = aggregate_plan(
            checks=checks,
            plan=_plan(_task("a")),
            config=loaded.plan,
            config_version=loaded.version,
            config_hash=loaded.digest,
        )
        assert result.decision is AssuranceDecision.needs_human
        assert result.blocking == [PlanCheckName.plan_risk]
        assert all(c.state is CheckState.passed for c in result.checks)

    def test_a_warn_never_silently_admits(self, loaded):
        """warn_allowed_checks is empty in v2, so any plan-level WARN blocks."""
        checks = _all_passing()
        checks[2] = _check(PlanCheckName.plan_consistent, CheckState.warn)
        result = aggregate_plan(
            checks=checks,
            plan=_plan(_task("a")),
            config=loaded.plan,
            config_version=loaded.version,
            config_hash=loaded.digest,
        )
        assert result.decision is AssuranceDecision.needs_human
        assert result.blocking == [PlanCheckName.plan_consistent]

    def test_a_permitted_warn_yields_execute_flagged(self, loaded):
        permissive = loaded.plan.model_copy(
            update={"warn_allowed_checks": [PlanCheckName.plan_consistent]}
        )
        checks = _all_passing()
        checks[2] = _check(PlanCheckName.plan_consistent, CheckState.warn)
        result = aggregate_plan(
            checks=checks,
            plan=_plan(_task("a")),
            config=permissive,
            config_version=loaded.version,
            config_hash=loaded.digest,
        )
        assert result.decision is AssuranceDecision.execute_flagged

    def test_all_passing_low_risk_admits(self, loaded):
        result = aggregate_plan(
            checks=_all_passing(),
            plan=_plan(_task("a")),
            config=loaded.plan,
            config_version=loaded.version,
            config_hash=loaded.digest,
        )
        assert result.decision is AssuranceDecision.execute
        assert result.admissible

    def test_a_check_that_did_not_run_blocks(self, loaded):
        partial = [c for c in _all_passing() if c.name is not PlanCheckName.coverage_complete]
        result = aggregate_plan(
            checks=partial,
            plan=_plan(_task("a")),
            config=loaded.plan,
            config_version=loaded.version,
            config_hash=loaded.digest,
        )
        assert result.decision is AssuranceDecision.needs_human
        assert len(result.checks) == 6

    def test_checks_are_always_in_fixed_order(self, loaded):
        result = aggregate_plan(
            checks=list(reversed(_all_passing())),
            plan=_plan(_task("a")),
            config=loaded.plan,
            config_version=loaded.version,
            config_hash=loaded.digest,
        )
        assert [c.name for c in result.checks] == list(PLAN_CHECK_ORDER)


class TestAdmissionIsNotAuthorisation:
    def test_the_result_says_so_structurally(self, loaded):
        result = evaluate_plan(
            plan=_plan(_task("a", refs=["flight:a"])),
            coverage=SAFE_COVERAGE,
            exposure=SAFE_EXPOSURE,
            config=loaded.plan,
            config_version=loaded.version,
            config_hash=loaded.digest,
        )
        assert result.authorises_no_action is True

    def test_an_admissible_plan_still_contains_tasks_awaiting_their_own_decision(self, loaded):
        """Admission means the plan may proceed to per-action authorisation, nothing more."""
        cash = _task(
            "cash",
            action="evaluate_entitlements",
            refs=["flight:a"],
            tier=RiskTier.high,
            kinds=["risk"],
            decision=AssuranceDecision.needs_human,
        )
        result = evaluate_plan(
            plan=_plan(cash),
            coverage=SAFE_COVERAGE,
            exposure=SAFE_EXPOSURE,
            config=loaded.plan,
            config_version=loaded.version,
            config_hash=loaded.digest,
        )
        # A high-risk task escalates the plan, so the plan itself needs a human too.
        assert result.requires_human
        assert result.plan_risk_tier is RiskTier.high


class TestEvaluatePlan:
    def test_the_cascade_plan_that_is_over_budget_is_refused(self, loaded):
        """Forty individually-fine medium actions committing more than the budget allows.

        The figures are set above the shipped ceilings rather than at fixed literals, so this test
        keeps testing "over budget" if the ceilings are recalibrated. It previously hard-coded
        340,000 against a 250,000 limit and silently became a pass when the limits were raised to
        fit the eight-flight network event.
        """
        limits = loaded.plan.limits
        tasks = [_task(str(n), refs=[f"hotel:{n}"]) for n in range(40)]
        result = evaluate_plan(
            plan=_plan(*tasks),
            coverage=CoverageDeclaration(
                declared=True, impacted_refs=[f"hotel:{n}" for n in range(40)]
            ),
            exposure=ExposureInputs(
                total_exposure_inr=limits.max_total_exposure_inr + 90000,
                passengers_affected=180,
                rooms_committed=62,
                external_effects=1,
            ),
            config=loaded.plan,
            config_version=loaded.version,
            config_hash=loaded.digest,
        )
        assert result.decision is AssuranceDecision.needs_human
        assert PlanCheckName.exposure_within_limits in result.blocking
        assert "EXPOSURE_LIMIT_BREACHED" in blocking_summary(result)

    def test_a_coverage_gap_is_refused_although_every_task_is_good(self, loaded):
        tasks = [_task(str(n), refs=[f"flight:{n}"]) for n in range(1, 6)]
        result = evaluate_plan(
            plan=_plan(*tasks),
            coverage=CoverageDeclaration(
                declared=True, impacted_refs=[f"flight:{n}" for n in range(1, 9)]
            ),
            exposure=SAFE_EXPOSURE,
            config=loaded.plan,
            config_version=loaded.version,
            config_hash=loaded.digest,
        )
        assert result.decision is AssuranceDecision.needs_human
        assert PlanCheckName.coverage_complete in result.blocking

    def test_a_clean_small_plan_is_admissible(self, loaded):
        result = evaluate_plan(
            plan=_plan(_task("a", refs=["flight:a"], tier=RiskTier.low)),
            coverage=SAFE_COVERAGE,
            exposure=SAFE_EXPOSURE,
            config=loaded.plan,
            config_version=loaded.version,
            config_hash=loaded.digest,
        )
        assert result.decision is AssuranceDecision.execute
        assert result.blocking == []

    def test_config_none_blocks_without_running_checks(self):
        result = evaluate_plan(
            plan=_plan(_task("a")), coverage=SAFE_COVERAGE, exposure=SAFE_EXPOSURE, config=None
        )
        assert result.decision is AssuranceDecision.needs_human
        assert all(c.reason_code is PlanReasonCode.PLAN_CONFIG_MISSING for c in result.checks)

    def test_the_same_plan_always_yields_the_same_decision(self, loaded):
        plan = _plan(_task("a", refs=["flight:a"]))
        first = evaluate_plan(
            plan=plan,
            coverage=SAFE_COVERAGE,
            exposure=SAFE_EXPOSURE,
            config=loaded.plan,
            config_version=loaded.version,
            config_hash=loaded.digest,
        )
        second = evaluate_plan(
            plan=plan,
            coverage=SAFE_COVERAGE,
            exposure=SAFE_EXPOSURE,
            config=loaded.plan,
            config_version=loaded.version,
            config_hash=loaded.digest,
        )
        assert first.model_dump(exclude={"evaluated_at"}) == second.model_dump(
            exclude={"evaluated_at"}
        )

    def test_evaluation_ids_are_recorded_for_the_audit_trail(self, loaded):
        plan = _plan(_task("a", refs=["flight:a"], evaluation_id=101))
        result = evaluate_plan(
            plan=plan,
            coverage=SAFE_COVERAGE,
            exposure=SAFE_EXPOSURE,
            config=loaded.plan,
            config_version=loaded.version,
            config_hash=loaded.digest,
        )
        assert result.task_evaluation_ids == [101]
        assert result.exposure["total_exposure_inr"] == 1000


class TestPlanHash:
    def test_it_is_stable(self):
        assert _plan(_task("a")).hash() == _plan(_task("a")).hash()

    def test_reordering_target_refs_does_not_invent_a_new_plan(self):
        one = _plan(_task("a", refs=["flight:1", "flight:2"]))
        other = _plan(_task("a", refs=["flight:2", "flight:1"]))
        assert one.hash() == other.hash()

    def test_changing_a_target_changes_the_hash(self):
        assert (
            _plan(_task("a", refs=["flight:1"])).hash()
            != _plan(_task("a", refs=["flight:2"])).hash()
        )

    def test_task_order_is_part_of_the_identity(self):
        one = _plan(_task("a", refs=["flight:a"]), _task("b", refs=["flight:b"]))
        other = _plan(_task("b", refs=["flight:b"]), _task("a", refs=["flight:a"]))
        assert one.hash() != other.hash()

    def test_adding_a_task_changes_the_hash(self):
        assert _plan(_task("a")).hash() != _plan(_task("a"), _task("b")).hash()

    def test_changing_a_dependency_changes_the_hash(self):
        one = _plan(_task("a"), _task("b"))
        other = _plan(_task("a"), _task("b", depends=["a"]))
        assert one.hash() != other.hash()

    def test_the_group_is_part_of_the_identity(self):
        assert _plan(_task("a"), group="G1").hash() != _plan(_task("a"), group="G2").hash()


class TestLoadPlanConfig:
    def test_v2_loads(self, loaded):
        assert loaded.version == "assurance-v2"
        assert loaded.plan.approval.high_risk_always_separate is True
        assert loaded.plan.warn_allowed_checks == []
        assert loaded.what_if.enabled is True

    def test_the_ceilings_admit_the_flagship_scenario(self, loaded):
        """A ceiling below the size of the demo event is not a safety property.

        A breach FAILs, and a FAIL is not approvable at plan level by anyone — so a ceiling under
        604 passengers turns "a human must accept this aggregate" into "nobody may accept it". The
        escalation fractions are what force a person to look; the ceiling is what the system may
        not commit at all.
        """
        limits = loaded.plan.limits
        assert limits.max_passengers_affected > 604, "the Bengaluru storm must reach a human"
        assert limits.max_total_exposure_inr > 0

    def test_the_flagship_scenario_still_escalates_to_a_human(self, loaded):
        """Admitted, but not waved through: 604/800 crosses the 0.6 passenger fraction."""
        limits = loaded.plan.limits
        fraction = 604 / limits.max_passengers_affected
        assert fraction > loaded.plan.escalation.passengers_fraction

    def test_v1_is_refused_because_it_predates_plan_assurance(self):
        """Defaulting the limits would invent a budget nobody approved."""
        with pytest.raises(AssuranceConfigMissing, match="no `plan` section"):
            load_plan_config(V1)

    def test_a_missing_file_raises(self):
        with pytest.raises(AssuranceConfigMissing):
            load_plan_config("/nonexistent/assurance.yaml")

    def test_unreadable_yaml_raises(self, tmp_path):
        broken = tmp_path / "broken.yaml"
        broken.write_text("plan: [unclosed\n", encoding="utf-8")
        with pytest.raises(AssuranceConfigMissing):
            load_plan_config(str(broken))

    def test_an_unrecognised_plan_key_raises(self, tmp_path):
        """A typo in safety config must not read as permissive."""
        typo = tmp_path / "typo.yaml"
        typo.write_text("version: v3\nplan:\n  limits:\n    max_everything: 9\n", encoding="utf-8")
        with pytest.raises(AssuranceConfigMissing):
            load_plan_config(str(typo))

    def test_the_digest_is_stable(self):
        assert load_plan_config(V2).digest == load_plan_config(V2).digest

    def test_v2_also_loads_through_the_action_level_loader(self):
        """One file can serve both levels.

        `AssuranceConfig` is extra="forbid", so v2's `plan:` and `what_if:` sections would have
        made it unloadable at action level — a trap for whoever eventually points
        `assurance_config_path` at it. The action loader now drops the plan-level sections and
        still rejects a genuine typo, so the alternative of duplicating `risk_tiers` into a
        second file is avoided.
        """
        from app.assurance.gate import load_config_with_digest

        config, _ = load_config_with_digest(V2)
        assert config.version == "assurance-v2"
        assert config.tier_for("evaluate_entitlements") is RiskTier.high
        assert config.freshness.metar_minutes == 60

    def test_a_typo_in_a_plan_config_is_still_rejected_at_action_level(self, tmp_path):
        typo = tmp_path / "typo.yaml"
        typo.write_text(
            "version: v3\nplan:\n  limits: {}\nwarn_allowed_everything: true\n", encoding="utf-8"
        )
        from app.assurance.gate import load_config_with_digest

        with pytest.raises(AssuranceConfigMissing):
            load_config_with_digest(str(typo))

    def test_v1_and_v2_are_different_configs(self):
        """v1 stays on disk so Phase 1 records remain interpretable."""
        from app.assurance.gate import load_config_with_digest

        v1_config, v1_digest = load_config_with_digest(V1)
        assert v1_config.version == "assurance-v1"
        assert v1_digest != load_plan_config(V2).digest


class TestTaskProjection:
    def test_it_classifies_the_block_so_no_caller_has_to(self):
        blocked = AssuranceResult(
            decision=AssuranceDecision.needs_human,
            risk_tier=RiskTier.medium,
            checks=[
                CheckResult(
                    name=CheckName.evidence_complete,
                    state=CheckState.failed,
                    reason_code=ReasonCode.MISSING_REQUIRED_FACT,
                )
            ],
            config_version="assurance-v2",
            config_hash="abc",
        )
        outcome = task_outcome_from(
            task_id="t1", action_type="reserve_hotel_block", result=blocked, evaluation_id=7
        )
        assert outcome.blocking_kinds == ["evidence"]
        assert outcome.approvable is False
        assert outcome.blocked_on_evidence_or_conflict
        assert outcome.evaluation_id == 7

    def test_a_risk_only_block_projects_as_approvable(self):
        risky = AssuranceResult(
            decision=AssuranceDecision.needs_human,
            risk_tier=RiskTier.high,
            checks=[
                CheckResult(
                    name=CheckName.action_risk,
                    state=CheckState.passed,
                    reason_code=ReasonCode.HUMAN_APPROVAL_REQUIRED,
                    tier=RiskTier.high,
                )
            ],
            config_version="assurance-v2",
            config_hash="abc",
        )
        outcome = task_outcome_from(task_id="t2", action_type="notify_passengers", result=risky)
        assert outcome.approvable is True
        assert not outcome.blocked_on_evidence_or_conflict
