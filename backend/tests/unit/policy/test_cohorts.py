"""Network-scale entitlement evaluation.

`test_one_unresolved_cohort_suppresses_the_whole_total` is the assertion that matters. A partial
total presented as a total looks authoritative and under-reports, and the plan-level exposure check
would then compare a real budget against a number missing an unknown amount.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from app.config import PolicyMode, Settings
from app.policy.cohorts import CohortRequest, calculate_cohorts, exposure_inputs_from
from app.policy.loader import load_pack

PACKS_ROOT = Path(__file__).resolve().parents[4] / "policy_packs"

#: A complete short-notice cancellation: block time 95 minutes, fare below the 7,500 cap.
CANCELLATION: dict[str, Any] = {
    "itinerary": {
        "origin_country": "IN",
        "destination_country": "IN",
        "scheduled_departure_local": "2026-08-20T21:10:00+05:30",
    },
    "operating_carrier": {"id": "AI", "country": "IN", "is_foreign": False},
    "event": {"type": "cancellation", "notice_minutes": 600, "travel_date": "2026-08-20"},
    "cancellation": {"notice_obligation_met": False},
    "flight": {"block_time_minutes": 95},
    "fare": {"one_way_basic_fare_inr": 4200, "airline_fuel_charge_inr": 800},
    "passenger": {"contact_info_provided_at_booking": True},
}


def _settings() -> Settings:
    return Settings(_env_file=None, policy_pack_dir=PACKS_ROOT)


@pytest.fixture(scope="module")
def pack():
    return load_pack(
        pack_dir=PACKS_ROOT,
        pack_id="in-moca-charter-2019",
        version="2019.02",
        mode=PolicyMode.charter,
    )


def _facts(**overrides: Any) -> dict[str, Any]:
    facts = copy.deepcopy(CANCELLATION)
    for family, values in overrides.items():
        if values is None:
            facts.pop(family, None)
        else:
            facts.setdefault(family, {}).update(values)
    return facts


def _cohort(cohort_id: str, facts: dict[str, Any], count: int, label: str | None = None):
    return CohortRequest(cohort_id=cohort_id, facts=facts, passenger_count=count, label=label)


def _run(cohorts, pack):
    return calculate_cohorts(cohorts=cohorts, pack=pack, settings=_settings())


class TestCohortArithmetic:
    def test_one_cohort_scales_the_cited_figure(self, pack):
        result = _run([_cohort("c1", _facts(), 40)], pack)
        assert result.cohorts[0].entitlement.cash_inr == 5000
        assert result.cohorts[0].cohort_exposure_inr == 200000
        assert result.exposure_inr == 200000
        assert result.passengers_covered == 40

    def test_several_cohorts_sum(self, pack):
        cohorts = [
            _cohort("c1", _facts(), 10),
            _cohort("c2", _facts(flight={"block_time_minutes": 200}), 5),
        ]
        result = _run(cohorts, pack)
        # 10 x 5000 (cap 7500 vs 5000) + 5 x 5000 (cap 10000 vs 5000)
        assert result.exposure_inr == 75000
        assert result.passengers_covered == 15

    def test_identical_facts_evaluate_once(self, pack):
        """174 passengers become one evaluation when their facts are the same."""
        cohorts = [_cohort("c1", _facts(), 100), _cohort("c2", _facts(), 74)]
        result = _run(cohorts, pack)
        assert result.evaluations_performed == 1
        assert len(result.cohorts) == 2
        assert result.cohorts[0].signature == result.cohorts[1].signature
        assert result.exposure_inr == 174 * 5000

    def test_key_order_does_not_create_a_second_signature(self, pack):
        reordered = {key: CANCELLATION[key] for key in reversed(list(CANCELLATION))}
        result = _run([_cohort("c1", _facts(), 1), _cohort("c2", reordered, 1)], pack)
        assert result.evaluations_performed == 1

    def test_distinct_facts_evaluate_separately(self, pack):
        cohorts = [
            _cohort("c1", _facts(), 1),
            _cohort("c2", _facts(flight={"block_time_minutes": 200}), 1),
        ]
        assert _run(cohorts, pack).evaluations_performed == 2

    def test_a_zero_cash_cohort_contributes_nothing_but_is_resolved(self, pack):
        resolved_zero = _facts(
            event={"notice_minutes": 30240}, cancellation={"notice_obligation_met": True}
        )
        result = _run([_cohort("c1", resolved_zero, 50)], pack)
        assert result.cohorts[0].entitlement.cash_inr == 0
        assert result.exposure_inr == 0
        assert result.exposure_established
        assert not result.requires_human

    def test_no_cohorts_is_an_established_zero(self, pack):
        result = _run([], pack)
        assert result.exposure_inr is None
        assert result.cohorts == []
        assert result.pack_version == "2019.02"


class TestFailClosed:
    def test_one_unresolved_cohort_suppresses_the_whole_total(self, pack):
        """A partial total would look authoritative and under-report."""
        unresolved = _facts(flight=None)  # block time absent
        cohorts = [_cohort("c1", _facts(), 100), _cohort("c2", unresolved, 20)]
        result = _run(cohorts, pack)

        assert result.exposure_inr is None
        assert not result.exposure_established
        assert result.requires_human
        assert result.unresolved_cohorts == ["c2"]

    def test_the_resolved_cohorts_stay_individually_visible(self, pack):
        """An operator can still see what is known and what is not."""
        cohorts = [_cohort("c1", _facts(), 100), _cohort("c2", _facts(flight=None), 20)]
        result = _run(cohorts, pack)
        resolved = next(c for c in result.cohorts if c.cohort_id == "c1")
        blocked = next(c for c in result.cohorts if c.cohort_id == "c2")
        assert resolved.cohort_exposure_inr == 500000
        assert blocked.cohort_exposure_inr is None
        assert blocked.requires_human

    def test_a_partially_evidenced_exemption_makes_a_cohort_unresolved(self, pack):
        """The weather case, at network scale."""
        claimed = _facts(
            cause_evidence={
                "operational_cause": "meteorological",
                "external_to_carrier": True,
                "unavoidable_despite_reasonable_measures": None,
            }
        )
        result = _run([_cohort("c1", claimed, 60)], pack)
        assert result.unresolved_cohorts == ["c1"]
        assert result.exposure_inr is None

    def test_every_cohort_carries_its_citation(self, pack):
        result = _run([_cohort("c1", _facts(), 10)], pack)
        cited = result.cohorts[0].entitlement
        assert cited.formula == "least_of_cap_and_basic_fare_plus_fuel_charge"
        assert cited.formula_used == "least_of(cap 7500, basic_fare 4200 + fuel 800) = 5000"
        assert cited.source_clause_refs
        assert cited.pack_version == "2019.02"

    def test_a_dated_pack_may_not_be_presented_as_current_law(self, pack):
        result = _run([_cohort("c1", _facts(), 10)], pack)
        assert result.pack_status == "official_guidance_dated"
        assert not result.cohorts[0].entitlement.may_be_presented_as_current_law

    def test_a_delay_cohort_never_pays_cash_across_the_network(self, pack):
        delay = {
            **CANCELLATION,
            "event": {
                "type": "delay",
                "delay_minutes": 420,
                "expected_delay_minutes": 420,
                "notice_minutes": 1500,
                "travel_date": "2026-08-20",
            },
            "flight": {"block_time_minutes": 165, "scheduled_departure_local_time": "21:10"},
            "passenger": {"checked_in_on_time": True},
        }
        result = _run([_cohort("c1", delay, 174)], pack)
        assert result.exposure_inr == 0

    def test_repeated_calls_agree(self, pack):
        cohorts = [_cohort("c1", _facts(), 10), _cohort("c2", _facts(flight=None), 3)]
        assert _run(cohorts, pack).model_dump() == _run(cohorts, pack).model_dump()

    def test_a_cohort_figure_matches_the_single_passenger_path(self, pack):
        """Cohorts must not reach a different figure than calculate would."""
        from app.policy.entitlements import calculate

        direct = calculate(facts=_facts(), pack=pack, settings=_settings())
        cohort = _run([_cohort("c1", _facts(), 1)], pack).cohorts[0].entitlement
        assert cohort.cash_inr == direct.cash_inr
        assert cohort.formula_used == direct.formula_used


class TestPlanExposureHandoff:
    def test_it_shapes_a_resolved_result_for_the_plan_gate(self, pack):
        result = _run([_cohort("c1", _facts(), 40)], pack)
        inputs = exposure_inputs_from(result, rooms_committed=12, external_effects=1)
        assert inputs["total_exposure_inr"] == 200000
        assert inputs["passengers_affected"] == 40
        assert inputs["rooms_committed"] == 12
        assert inputs["unresolved_cohorts"] == []

    def test_an_unresolved_result_hands_over_nothing_to_compare(self, pack):
        result = _run([_cohort("c1", _facts(flight=None), 40)], pack)
        inputs = exposure_inputs_from(result, rooms_committed=12, external_effects=0)
        assert inputs["total_exposure_inr"] is None
        assert inputs["passengers_affected"] is None
        assert inputs["unresolved_cohorts"] == ["c1"]

    def test_omitted_operational_figures_stay_none(self, pack):
        """Stream B never derives a room count, and None is treated as a breach, not a zero."""
        result = _run([_cohort("c1", _facts(), 40)], pack)
        inputs = exposure_inputs_from(result)
        assert inputs["rooms_committed"] is None
        assert inputs["external_effects"] is None

    def test_the_handoff_feeds_the_plan_gate_end_to_end(self, pack):
        """Unresolved cohorts must make the plan's exposure check fail."""
        from app.assurance.plan_checks import exposure_within_limits
        from app.assurance.plan_contract import (
            ExposureInputs,
            PlanConfig,
            PlanReasonCode,
            TaskOutcome,
        )
        from app.models.enums import AssuranceDecision, CheckState, RiskTier

        result = _run([_cohort("c1", _facts(flight=None), 40)], pack)
        exposure = ExposureInputs(
            **exposure_inputs_from(result, rooms_committed=1, external_effects=0)
        )
        task = TaskOutcome(
            task_id="t1",
            action_type="evaluate_entitlements",
            decision=AssuranceDecision.execute,
            risk_tier=RiskTier.high,
        )
        check = exposure_within_limits(tasks=[task], exposure=exposure, config=PlanConfig())
        assert check.state is CheckState.failed
        assert check.reason_code is PlanReasonCode.EXPOSURE_UNKNOWN
