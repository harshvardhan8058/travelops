"""Derived requirements, and proof that they block execution.

Two halves. The first asserts that the derivation is grounded in the pack: the right rules are
selected, the facts they read are demanded, and the provenance says which rule demanded what.
The second half is the one that matters — it runs the real gate and proves that a missing
required fact or a violated constraint produces `needs_human` rather than a green tick.

`TestNothingPassesVacuously` is the regression guard for the bug this exists to fix: empty
`required_facts` and `constraints` made `evidence_complete` and `policy_compliant` pass while
verifying nothing.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from app.assurance.contract import CheckName, ReasonCode
from app.assurance.gate import GateInputs, evaluate, load_config_with_digest
from app.config import PolicyMode, Settings
from app.models.enums import AssuranceDecision, CheckState
from app.policy.engine import absent_facts
from app.policy.loader import load_pack
from app.policy.requirements import (
    CONSTRAINT_CASH_MATCHES_ENGINE,
    CONSTRAINT_EVALUATION_BLOCKED,
    CONSTRAINT_NO_EXCLUDED_RULES,
    CONSTRAINT_NOT_CURRENT_LAW,
    CONSTRAINT_PACK_VERSION,
    ORIGIN_APPLICABILITY,
    ORIGIN_EXEMPTION_EVIDENCE,
    ORIGIN_FORMULA_INPUT,
    ORIGIN_RULE_CONDITION,
    gate_requirements,
)

PACKS_ROOT = Path(__file__).resolve().parents[4] / "policy_packs"
EXCLUDED_RULE = "booking.free_cancel_or_amend_within_24h"

#: A complete short-notice cancellation: block time 95 minutes, fare below the 7,500 cap.
FULL_FACTS: dict[str, Any] = {
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

#: The payload a caller must produce for an entitlement action, consistent with the law.
COMPLIANT_PAYLOAD: dict[str, Any] = {
    "pack_version": "2019.02",
    "cited_rule_ids": ["cancellation.compensation.block_60_to_120"],
    "presented_as_current_law": False,
    "cash_inr": 5000,
    "currency": "INR",
}


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, policy_pack_dir=PACKS_ROOT, **overrides)


def _facts(**removals: list[str]) -> dict[str, Any]:
    """A copy of the full facts with named leaves removed, e.g. flight=["block_time_minutes"]."""
    facts = copy.deepcopy(FULL_FACTS)
    for family, leaves in removals.items():
        for leaf in leaves:
            facts[family].pop(leaf, None)
    return facts


@pytest.fixture(scope="module")
def pack():
    return load_pack(
        pack_dir=PACKS_ROOT,
        pack_id="in-moca-charter-2019",
        version="2019.02",
        mode=PolicyMode.charter,
    )


@pytest.fixture(scope="module")
def gate_config():
    return load_config_with_digest("./config/assurance.v1.yaml")


def _requirements(facts: dict[str, Any], pack, action: str = "evaluate_entitlements"):
    return gate_requirements(action_type=action, facts=facts, pack=pack, settings=_settings())


def _run_gate(
    facts: dict[str, Any], pack, gate_config, *, payload=None, action="evaluate_entitlements"
):
    """Derive requirements, then put them through the real gate — the integrated path."""
    config, digest = gate_config
    requirements = _requirements(facts, pack, action)
    return evaluate(
        inputs=GateInputs(
            action_type=action,
            required_facts=requirements.required_facts,
            provided_facts=facts,
            constraints=requirements.constraints,
            payload=COMPLIANT_PAYLOAD if payload is None else payload,
        ),
        config=config,
        config_hash=digest,
    )


def _check(result, name: CheckName):
    return next(item for item in result.checks if item.name is name)


# ------------------------------------------------------------------- derivation is grounded


class TestCanonicalSelection:
    def test_the_resolver_pack_is_named_with_its_hash(self, pack):
        requirements = _requirements(FULL_FACTS, pack)
        assert requirements.pack_id == "in-moca-charter-2019"
        assert requirements.pack_version == "2019.02"
        assert requirements.pack_hash == pack.pack_hash
        assert requirements.pack_status == "official_guidance_dated"
        assert requirements.applicability_status == "applicable"
        assert requirements.resolver_version == "resolver-v1"

    def test_the_canonical_cash_rule_is_selected(self, pack):
        """Block time 95 minutes puts this in the 7,500 band and nowhere else."""
        selected = _requirements(FULL_FACTS, pack).selected_rule_ids
        assert "cancellation.compensation.block_60_to_120" in selected
        assert "cancellation.compensation.block_upto_60" not in selected
        assert "cancellation.compensation.block_over_120" not in selected

    def test_an_unknown_band_selects_every_candidate(self, pack):
        """Without block time all three bands are live, so all three demand their facts."""
        selected = _requirements(_facts(flight=["block_time_minutes"]), pack).selected_rule_ids
        assert {
            "cancellation.compensation.block_upto_60",
            "cancellation.compensation.block_60_to_120",
            "cancellation.compensation.block_over_120",
        } <= set(selected)

    def test_rules_ruled_out_by_a_known_fact_are_not_selected(self, pack):
        """A cancellation must not drag in delay or denied-boarding facts."""
        requirements = _requirements(FULL_FACTS, pack)
        assert not [rule for rule in requirements.selected_rule_ids if rule.startswith("delay.")]
        assert not [
            rule for rule in requirements.selected_rule_ids if rule.startswith("denied_boarding.")
        ]
        assert not [path for path in requirements.required_facts if path.startswith("alternate_")]

    def test_the_excluded_rule_is_never_selected_but_is_reported(self, pack):
        requirements = _requirements(FULL_FACTS, pack)
        assert EXCLUDED_RULE not in requirements.selected_rule_ids
        assert requirements.excluded_rule_ids == [EXCLUDED_RULE]


class TestRequiredFacts:
    def test_applicability_facts_are_always_demanded(self, pack):
        requirements = _requirements(FULL_FACTS, pack)
        for path in pack.required_facts:
            assert path in requirements.required_facts

    def test_the_deciding_rule_contributes_its_condition_facts(self, pack):
        requirements = _requirements(FULL_FACTS, pack)
        assert {
            "cancellation.notice_obligation_met",
            "flight.block_time_minutes",
            "passenger.contact_info_provided_at_booking",
        } <= set(requirements.required_facts)

    def test_formula_inputs_are_demanded(self, pack):
        requirements = _requirements(FULL_FACTS, pack)
        assert {"fare.one_way_basic_fare_inr", "fare.airline_fuel_charge_inr"} <= set(
            requirements.required_facts
        )

    def test_every_demanded_fact_records_why(self, pack):
        requirements = _requirements(FULL_FACTS, pack)
        origins = {req.path: req.origin for req in requirements.requirements}
        assert origins["itinerary.origin_country"] == ORIGIN_APPLICABILITY
        assert origins["flight.block_time_minutes"] == ORIGIN_RULE_CONDITION
        assert origins["fare.one_way_basic_fare_inr"] == ORIGIN_FORMULA_INPUT

    def test_provenance_names_the_demanding_rule(self, pack):
        requirements = _requirements(FULL_FACTS, pack)
        block_time = next(
            req for req in requirements.requirements if req.path == "flight.block_time_minutes"
        )
        assert "cancellation.compensation.block_60_to_120" in block_time.demanded_by

    def test_a_complete_fact_set_satisfies_every_demand(self, pack):
        """The requirements must be satisfiable, or the gate would block forever."""
        requirements = _requirements(FULL_FACTS, pack)
        assert absent_facts(FULL_FACTS, requirements.required_facts) == []

    def test_care_facts_are_deliberately_not_demanded(self, pack):
        """A care entitlement left unestablished is reported, not blocked.

        Demanding these would block every incompletely recorded disruption and teach people to
        route around the gate. The line is that the gate demands facts that determine a figure.
        """
        requirements = _requirements(FULL_FACTS, pack)
        assert "passenger.reported_for_original_flight" not in requirements.required_facts
        assert "alternate_flight.offered" not in requirements.required_facts


class TestExemptionEvidence:
    def test_evidence_is_not_demanded_when_no_exemption_was_asserted(self, pack):
        """The common case. Demanding it here would stall every ordinary cancellation."""
        requirements = _requirements(FULL_FACTS, pack)
        assert not [
            path for path in requirements.required_facts if path.startswith("cause_evidence.")
        ]
        assert "exemption.force_majeure" not in requirements.selected_rule_ids

    def test_evidence_is_demanded_once_the_claim_is_in_play(self, pack):
        """One asserted fact means the exemption is being claimed, so prove it."""
        facts = copy.deepcopy(FULL_FACTS)
        facts["cause_evidence"] = {
            "operational_cause": "meteorological",
            "external_to_carrier": True,
            "unavoidable_despite_reasonable_measures": None,
        }
        requirements = _requirements(facts, pack)
        assert "exemption.force_majeure" in requirements.selected_rule_ids
        assert "cause_evidence.unavoidable_despite_reasonable_measures" in (
            requirements.required_facts
        )
        assert "cause_evidence.evidence_refs" in requirements.required_facts
        origins = {req.path: req.origin for req in requirements.requirements}
        assert origins["cause_evidence.evidence_refs"] == ORIGIN_EXEMPTION_EVIDENCE

    def test_an_exemption_refused_on_the_facts_demands_nothing_extra(self, pack):
        facts = copy.deepcopy(FULL_FACTS)
        facts["cause_evidence"] = {
            "operational_cause": "crew_rostering",
            "external_to_carrier": False,
            "unavoidable_despite_reasonable_measures": False,
            "evidence_refs": ["ops:1"],
        }
        requirements = _requirements(facts, pack)
        assert absent_facts(facts, requirements.required_facts) == []


class TestDerivedConstraints:
    def test_the_pack_version_must_be_cited(self, pack):
        ids = {c["id"] for c in _requirements(FULL_FACTS, pack).constraints}
        assert CONSTRAINT_PACK_VERSION in ids

    def test_the_engine_figure_is_asserted(self, pack):
        constraint = next(
            c
            for c in _requirements(FULL_FACTS, pack).constraints
            if c["id"] == CONSTRAINT_CASH_MATCHES_ENGINE
        )
        assert constraint["value"] == 5000

    def test_no_figure_is_asserted_when_the_engine_produced_none(self, pack):
        ids = {
            c["id"] for c in _requirements(_facts(flight=["block_time_minutes"]), pack).constraints
        }
        assert CONSTRAINT_CASH_MATCHES_ENGINE not in ids

    def test_a_dated_pack_forbids_claiming_current_law(self, pack):
        ids = {c["id"] for c in _requirements(FULL_FACTS, pack).constraints}
        assert CONSTRAINT_NOT_CURRENT_LAW in ids

    def test_excluded_rules_may_not_be_cited(self, pack):
        constraint = next(
            c
            for c in _requirements(FULL_FACTS, pack).constraints
            if c["id"] == CONSTRAINT_NO_EXCLUDED_RULES
        )
        assert constraint["value"] == [EXCLUDED_RULE]


class TestNonPolicyActions:
    @pytest.mark.parametrize(
        "action", ["check_connections", "reserve_hotel_block", "notify_passengers"]
    )
    def test_the_pack_has_nothing_to_say(self, pack, action: str):
        """Business limits for these live in Stream C's data, not in a statutory pack."""
        requirements = _requirements(FULL_FACTS, pack, action)
        assert requirements.policy_bearing is False
        assert requirements.required_facts == []
        assert requirements.constraints == []


class TestFailClosedDerivation:
    def test_verified_mode_yields_a_blocking_constraint_rather_than_raising(self):
        """A caller gets requirements that fail closed, not an exception to remember to catch."""
        requirements = gate_requirements(
            action_type="evaluate_entitlements",
            facts=FULL_FACTS,
            settings=_settings(policy_mode="charter"),
        )
        # Charter mode loads; now force the ineligible path explicitly.
        assert requirements.policy_bearing

        blocked = gate_requirements(
            action_type="evaluate_entitlements",
            facts=FULL_FACTS,
            settings=_settings(policy_pack_version="does-not-exist"),
        )
        assert blocked.blocking_reasons == ["POLICY_PACK_UNAVAILABLE"]
        assert blocked.constraints[0]["pack_unavailable"] is True

    def test_a_deferred_jurisdiction_is_carried_as_an_unsatisfiable_constraint(self, pack):
        """A block that is not shaped like a missing fact still has to reach the gate.

        Every demanded fact is present here, so without this constraint the gate would find
        nothing wrong and authorise an action the policy layer had already refused.
        """
        facts = copy.deepcopy(FULL_FACTS)
        facts["operating_carrier"]["is_foreign"] = True
        requirements = _requirements(facts, pack)

        assert requirements.blocking_reasons == ["DEFERS_TO_OTHER_JURISDICTION"]
        assert absent_facts(facts, requirements.required_facts) == []
        unsatisfiable = next(
            c for c in requirements.constraints if c["id"] == CONSTRAINT_EVALUATION_BLOCKED
        )
        assert unsatisfiable["unsatisfiable"] is True

    def test_facts_the_engine_reported_missing_are_always_demanded(self, pack):
        """The engine is the authority on what stopped it."""
        facts = copy.deepcopy(FULL_FACTS)
        facts["cause_evidence"] = {
            "external_to_carrier": True,
            "unavoidable_despite_reasonable_measures": None,
        }
        requirements = _requirements(facts, pack)
        assert "cause_evidence.unavoidable_despite_reasonable_measures" in (
            requirements.required_facts
        )

    def test_derivation_is_reproducible(self, pack):
        first = _requirements(FULL_FACTS, pack)
        second = _requirements(FULL_FACTS, pack)
        assert first.model_dump() == second.model_dump()


# ---------------------------------------------------- proof that the gate actually blocks


class TestMissingRequiredFactsBlockExecution:
    def test_the_satisfiable_baseline_passes_every_verifiable_check(self, pack, gate_config):
        """The control for every test below.

        evaluate_entitlements is high risk, so it still needs a human — but the five
        verifiable checks pass, which proves the requirements are satisfiable rather than
        permanently blocking.
        """
        result = _run_gate(FULL_FACTS, pack, gate_config)
        assert _check(result, CheckName.evidence_complete).state is CheckState.passed
        assert _check(result, CheckName.policy_compliant).state is CheckState.passed
        assert result.blocking == [CheckName.action_risk]
        assert result.decision is AssuranceDecision.needs_human

    def test_missing_block_time_blocks(self, pack, gate_config):
        result = _run_gate(_facts(flight=["block_time_minutes"]), pack, gate_config)
        evidence = _check(result, CheckName.evidence_complete)
        assert evidence.state is CheckState.failed
        assert evidence.reason_code is ReasonCode.MISSING_REQUIRED_FACT
        assert evidence.reason is not None and "flight.block_time_minutes" in evidence.reason
        assert result.decision is AssuranceDecision.needs_human
        assert not result.executable

    def test_missing_fare_components_block(self, pack, gate_config):
        result = _run_gate(
            _facts(fare=["one_way_basic_fare_inr", "airline_fuel_charge_inr"]), pack, gate_config
        )
        assert _check(result, CheckName.evidence_complete).state is CheckState.failed
        assert not result.executable

    def test_a_null_fact_blocks_exactly_like_an_absent_one(self, pack, gate_config):
        facts = copy.deepcopy(FULL_FACTS)
        facts["flight"]["block_time_minutes"] = None
        result = _run_gate(facts, pack, gate_config)
        assert _check(result, CheckName.evidence_complete).state is CheckState.failed

    def test_the_weather_case_blocks_at_the_gate_not_only_in_the_engine(self, pack, gate_config):
        """The demo case, reaching the assurance panel.

        A meteorological cause is asserted and the decisive fact is null. The gate's own
        evidence check fails, so the block is visible where an operator looks.
        """
        facts = copy.deepcopy(FULL_FACTS)
        facts["cause_evidence"] = {
            "operational_cause": "meteorological",
            "external_to_carrier": True,
            "unavoidable_despite_reasonable_measures": None,
        }
        result = _run_gate(facts, pack, gate_config)
        evidence = _check(result, CheckName.evidence_complete)
        assert evidence.state is CheckState.failed
        assert evidence.reason is not None
        assert "unavoidable_despite_reasonable_measures" in evidence.reason
        assert result.decision is AssuranceDecision.needs_human

    def test_an_applicability_fact_missing_blocks(self, pack, gate_config):
        result = _run_gate(_facts(operating_carrier=["id"]), pack, gate_config)
        assert _check(result, CheckName.evidence_complete).state is CheckState.failed


class TestViolatedConstraintsBlockExecution:
    def test_a_figure_that_differs_from_the_engine_blocks(self, pack, gate_config):
        """Nothing between the engine and execution may change the number."""
        payload = {**COMPLIANT_PAYLOAD, "cash_inr": 7500}
        result = _run_gate(FULL_FACTS, pack, gate_config, payload=payload)
        policy = _check(result, CheckName.policy_compliant)
        assert policy.state is CheckState.failed
        assert policy.reason_code is ReasonCode.POLICY_CONSTRAINT_BREACH
        assert policy.reason is not None and CONSTRAINT_CASH_MATCHES_ENGINE in policy.reason
        assert not result.executable

    def test_citing_the_superseded_rule_blocks(self, pack, gate_config):
        payload = {**COMPLIANT_PAYLOAD, "cited_rule_ids": [EXCLUDED_RULE]}
        result = _run_gate(FULL_FACTS, pack, gate_config, payload=payload)
        policy = _check(result, CheckName.policy_compliant)
        assert policy.state is CheckState.failed
        assert policy.reason is not None and CONSTRAINT_NO_EXCLUDED_RULES in policy.reason

    def test_claiming_current_law_blocks(self, pack, gate_config):
        payload = {**COMPLIANT_PAYLOAD, "presented_as_current_law": True}
        result = _run_gate(FULL_FACTS, pack, gate_config, payload=payload)
        assert _check(result, CheckName.policy_compliant).state is CheckState.failed

    def test_citing_the_wrong_pack_version_blocks(self, pack, gate_config):
        payload = {**COMPLIANT_PAYLOAD, "pack_version": "2026.02"}
        result = _run_gate(FULL_FACTS, pack, gate_config, payload=payload)
        assert _check(result, CheckName.policy_compliant).state is CheckState.failed

    def test_the_wrong_currency_blocks(self, pack, gate_config):
        payload = {**COMPLIANT_PAYLOAD, "currency": "USD"}
        result = _run_gate(FULL_FACTS, pack, gate_config, payload=payload)
        assert _check(result, CheckName.policy_compliant).state is CheckState.failed

    def test_a_payload_missing_a_constrained_field_blocks(self, pack, gate_config):
        """Silence is not compliance."""
        payload = {key: value for key, value in COMPLIANT_PAYLOAD.items() if key != "cash_inr"}
        result = _run_gate(FULL_FACTS, pack, gate_config, payload=payload)
        policy = _check(result, CheckName.policy_compliant)
        assert policy.state is CheckState.failed
        assert policy.reason_code is ReasonCode.MISSING_REQUIRED_FACT

    def test_a_deferred_jurisdiction_blocks_though_every_fact_is_present(self, pack, gate_config):
        facts = copy.deepcopy(FULL_FACTS)
        facts["operating_carrier"]["is_foreign"] = True
        result = _run_gate(facts, pack, gate_config)
        assert _check(result, CheckName.evidence_complete).state is CheckState.passed
        assert _check(result, CheckName.policy_compliant).state is CheckState.failed
        assert not result.executable

    def test_an_unloadable_pack_blocks(self, pack, gate_config):
        config, digest = gate_config
        requirements = gate_requirements(
            action_type="evaluate_entitlements",
            facts=FULL_FACTS,
            settings=_settings(policy_pack_version="does-not-exist"),
        )
        result = evaluate(
            inputs=GateInputs(
                action_type="evaluate_entitlements",
                required_facts=requirements.required_facts,
                provided_facts=FULL_FACTS,
                constraints=requirements.constraints,
                payload=COMPLIANT_PAYLOAD,
            ),
            config=config,
            config_hash=digest,
        )
        policy = _check(result, CheckName.policy_compliant)
        assert policy.state is CheckState.failed
        assert policy.reason_code is ReasonCode.POLICY_PACK_UNAVAILABLE
        assert not result.executable


class TestNothingPassesVacuously:
    """The regression guard for the bug this module exists to fix."""

    def test_empty_requirements_on_an_entitlement_action_are_refused(self, gate_config):
        """The exact input Stream A was sending: both lists empty."""
        config, digest = gate_config
        result = evaluate(
            inputs=GateInputs(
                action_type="evaluate_entitlements", required_facts=[], constraints=[]
            ),
            config=config,
            config_hash=digest,
        )
        evidence = _check(result, CheckName.evidence_complete)
        policy = _check(result, CheckName.policy_compliant)

        assert evidence.state is CheckState.failed
        assert policy.state is CheckState.failed
        assert evidence.reason is not None and "gate_requirements" in evidence.reason
        assert result.decision is AssuranceDecision.needs_human
        assert not result.executable

    def test_facts_without_constraints_are_still_refused(self, gate_config):
        config, digest = gate_config
        result = evaluate(
            inputs=GateInputs(
                action_type="evaluate_entitlements",
                required_facts=["event.type"],
                provided_facts={"event": {"type": "cancellation"}},
                constraints=[],
            ),
            config=config,
            config_hash=digest,
        )
        assert _check(result, CheckName.policy_compliant).state is CheckState.failed

    def test_constraints_without_facts_are_still_refused(self, gate_config):
        config, digest = gate_config
        result = evaluate(
            inputs=GateInputs(
                action_type="evaluate_entitlements",
                required_facts=[],
                constraints=[{"field": "pack_version", "op": "eq", "value": "2019.02"}],
                payload={"pack_version": "2019.02"},
            ),
            config=config,
            config_hash=digest,
        )
        assert _check(result, CheckName.evidence_complete).state is CheckState.failed

    def test_a_non_policy_action_is_not_penalised_for_empty_requirements(self, gate_config):
        """There is genuinely nothing for a pack to say about checking connections."""
        config, digest = gate_config
        result = evaluate(
            inputs=GateInputs(action_type="check_connections"),
            config=config,
            config_hash=digest,
        )
        assert _check(result, CheckName.evidence_complete).state is CheckState.passed
        assert _check(result, CheckName.policy_compliant).state is CheckState.passed
        assert result.decision is AssuranceDecision.execute

    def test_derived_requirements_are_never_vacuous_for_an_entitlement_action(self, pack):
        for facts in (FULL_FACTS, _facts(flight=["block_time_minutes"]), {}):
            requirements = _requirements(facts, pack)
            assert not requirements.is_vacuous
