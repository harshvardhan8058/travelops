"""Candidate recovery-plan evaluation.

`TestNoPreference` is the boundary this module exists to hold: B decides admissibility and never
preference. If a score or a `recommended` flag ever appears, the operator starts approving the
gate's opinion instead of making their own decision.
"""

from __future__ import annotations

from app.assurance.candidates import CandidateInput, evaluate_candidates
from app.assurance.plan_contract import (
    CoverageDeclaration,
    ExposureInputs,
    PlanCheckName,
    PlanUnderReview,
    TaskOutcome,
    WhatIfPolicy,
)
from app.assurance.plan_gate import load_plan_config
from app.models.enums import AssuranceDecision, RiskTier

LOADED = load_plan_config("./config/assurance.v2.yaml")
POLICY = LOADED.what_if
SEED = 20260807


def _task(
    task_id: str,
    *,
    action: str = "reserve_hotel_block",
    refs: list[str] | None = None,
    tier: RiskTier = RiskTier.medium,
    kinds: list[str] | None = None,
    decision: AssuranceDecision = AssuranceDecision.execute,
) -> TaskOutcome:
    return TaskOutcome(
        task_id=task_id,
        action_type=action,
        target_refs=refs if refs is not None else [f"flight:{task_id}"],
        decision=decision,
        risk_tier=tier,
        blocking_kinds=kinds or [],
        approvable=kinds == ["risk"],
    )


def _candidate(
    candidate_id: str,
    *,
    tasks: list[TaskOutcome] | None = None,
    exposure: ExposureInputs | None = None,
    impacted: list[str] | None = None,
) -> CandidateInput:
    tasks = tasks or [_task("a", refs=["flight:1"])]
    return CandidateInput(
        candidate_id=candidate_id,
        plan=PlanUnderReview(group_reference="GRP-1", tasks=tasks),
        coverage=CoverageDeclaration(
            declared=True, impacted_refs=impacted if impacted is not None else ["flight:1"]
        ),
        exposure=exposure
        or ExposureInputs(
            total_exposure_inr=5000, passengers_affected=20, rooms_committed=4, external_effects=0
        ),
    )


def _run(candidates: list[CandidateInput], **overrides):
    payload = {
        "candidates": candidates,
        "config": LOADED.plan,
        "config_version": LOADED.version,
        "config_hash": LOADED.digest,
        "what_if_policy": POLICY,
        "seed": SEED,
    }
    payload.update(overrides)
    return evaluate_candidates(**payload)


class TestAdmissibility:
    def test_every_candidate_gets_its_own_evaluation(self):
        result = _run([_candidate("A"), _candidate("B")])
        assert len(result.evaluations) == 2
        assert len({e.plan_hash for e in result.evaluations}) == 1, "identical plans, one hash"
        assert result.decision == "proceed"

    def test_an_admissible_and_an_inadmissible_candidate_are_distinguished(self):
        over_budget = _candidate(
            "B",
            exposure=ExposureInputs(
                total_exposure_inr=900000,
                passengers_affected=20,
                rooms_committed=4,
                external_effects=0,
            ),
        )
        result = _run([_candidate("A"), over_budget])
        assert result.admissible == ["A"]
        assert result.decision == "proceed"

    def test_zero_admissible_candidates_needs_a_human(self):
        """Never the closest match."""
        broken = _candidate("A", impacted=["flight:1", "flight:2", "flight:3"])
        result = _run([broken])
        assert result.admissible == []
        assert result.decision == "needs_human"
        assert result.requires_human
        assert PlanCheckName.coverage_complete.value in result.blocking_reasons

    def test_admissible_order_follows_input_order_not_rank(self):
        result = _run([_candidate("C"), _candidate("A"), _candidate("B")])
        assert result.admissible == ["C", "A", "B"]

    def test_an_empty_candidate_list_needs_a_human(self):
        result = _run([])
        assert result.decision == "needs_human"


class TestNoPreference:
    def test_the_contract_contains_no_score_or_recommendation(self):
        """Asserted structurally so it cannot be added without this test failing."""
        from app.assurance.candidates import CandidateComparison, CandidateSet

        forbidden = {"score", "rank", "ranking", "recommended", "recommendation", "best", "weight"}
        for model in (CandidateComparison, CandidateSet):
            assert not forbidden & set(model.model_fields), model.__name__

    def test_comparison_is_arithmetic_a_reviewer_can_redo_by_hand(self):
        tasks = [
            _task("a", refs=["flight:1"]),
            _task(
                "cash",
                action="evaluate_entitlements",
                refs=["flight:1"],
                tier=RiskTier.high,
                kinds=["risk"],
                decision=AssuranceDecision.needs_human,
            ),
        ]
        result = _run([_candidate("A", tasks=tasks)])
        row = result.comparison[0]
        assert row.candidate_id == "A"
        assert row.task_count == 2
        assert row.high_risk_actions == 1
        assert row.approvals_required == 1
        assert row.exposure_inr == 5000
        assert row.uncovered_entities == 0

    def test_uncovered_entities_are_counted_for_comparison(self):
        result = _run([_candidate("A", impacted=["flight:1", "flight:2", "flight:3"])])
        assert result.comparison[0].uncovered_entities == 2

    def test_every_candidate_appears_in_the_comparison_even_when_inadmissible(self):
        over_budget = _candidate(
            "B",
            exposure=ExposureInputs(
                total_exposure_inr=900000,
                passengers_affected=20,
                rooms_committed=4,
                external_effects=0,
            ),
        )
        result = _run([_candidate("A"), over_budget])
        assert [row.candidate_id for row in result.comparison] == ["A", "B"]
        assert result.comparison[1].admissible is False
        assert result.comparison[1].blocking_checks


class TestZeroWriteGuardRunsFirst:
    def test_a_live_provider_stops_the_comparison_entirely(self):
        result = _run([_candidate("A")], provider_modes={"weather": "live"})
        assert result.decision == "needs_human"
        assert result.evaluations == []
        assert result.comparison == []
        assert "WHATIF_PROVIDER_LIVE" in result.blocking_reasons

    def test_a_missing_seed_stops_the_comparison(self):
        result = _run([_candidate("A")], seed=None)
        assert result.decision == "needs_human"
        assert result.evaluations == []

    def test_armed_dispatch_stops_the_comparison(self):
        result = _run([_candidate("A")], real_dispatch_enabled=True)
        assert result.evaluations == []

    def test_more_candidates_than_configured_stops_the_comparison(self):
        result = _run([_candidate(str(n)) for n in range(9)])
        assert result.decision == "needs_human"
        assert "WHATIF_TOO_MANY_CANDIDATES" in result.blocking_reasons

    def test_a_disabled_policy_stops_the_comparison(self):
        result = _run([_candidate("A")], what_if_policy=WhatIfPolicy(enabled=False))
        assert result.decision == "needs_human"
        assert result.evaluations == []

    def test_the_verdict_is_recorded_on_the_result(self):
        result = _run([_candidate("A")])
        assert result.what_if is not None
        assert result.what_if.permitted
        assert result.what_if.seed == SEED
        assert result.what_if.provenance == "simulated"
        assert result.what_if.authoritative is False


class TestFailClosed:
    def test_missing_plan_config_admits_nothing(self):
        result = _run([_candidate("A")], config=None)
        assert result.admissible == []
        assert result.decision == "needs_human"

    def test_an_unevaluable_candidate_makes_the_whole_set_undecidable(self, monkeypatch):
        """Selecting from the remainder would be choosing without knowing what was excluded."""
        from app.assurance import candidates as module

        real = module.evaluate_plan

        def explode_on_b(*, plan, **kwargs):
            if any(task.task_id == "boom" for task in plan.tasks):
                raise RuntimeError("cannot evaluate this candidate")
            return real(plan=plan, **kwargs)

        monkeypatch.setattr(module, "evaluate_plan", explode_on_b)

        result = _run([_candidate("A"), _candidate("B", tasks=[_task("boom", refs=["flight:1"])])])
        assert result.decision == "needs_human"
        assert result.admissible == []
        assert any("CANDIDATE_EVALUATION_FAILED" in reason for reason in result.blocking_reasons)

    def test_the_same_candidates_yield_the_same_result(self):
        first = _run([_candidate("A"), _candidate("B")])
        second = _run([_candidate("A"), _candidate("B")])
        assert first.model_dump(
            exclude={"evaluations": {"__all__": {"evaluated_at"}}}
        ) == second.model_dump(exclude={"evaluations": {"__all__": {"evaluated_at"}}})

    def test_no_evaluation_authorises_an_action(self):
        result = _run([_candidate("A")])
        assert all(e.authorises_no_action is True for e in result.evaluations)
