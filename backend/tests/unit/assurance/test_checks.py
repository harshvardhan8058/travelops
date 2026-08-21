"""The six assurance checks, in isolation.

Every check is exercised four ways: the passing case, the failing case, the boundary value
and the missing-input case. The checks are pure, so no fixture here touches a clock, a
database or a network.

Two tests carry more weight than the rest:

  * `test_explicit_false_is_an_answer_but_none_is_not` — the null-is-not-a-legal-answer rule.
  * `test_undated_source_is_not_downgraded_even_when_warn_is_permitted` — the config
    tolerance covers known staleness, never unknown age.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.assurance.checks import (
    action_risk,
    entities_valid,
    evidence_complete,
    no_conflicts,
    policy_compliant,
    sources_fresh,
)
from app.assurance.contract import AssuranceConfig, CheckName, ReasonCode
from app.models.enums import ActionType, CheckState, RiskTier, TaskState

NOW = datetime(2026, 8, 20, 15, 40, tzinfo=UTC)

# Mirrors config/assurance.v1.yaml. Built by hand rather than loaded so an edit to the real
# file cannot quietly make these assertions vacuous — the loaded-config path is covered
# separately in test_gate.py.
CONFIG = AssuranceConfig(
    version="assurance-v1-test",
    risk_tiers={
        "check_connections": RiskTier.low,
        "find_hotel_options": RiskTier.low,
        "reserve_hotel_block": RiskTier.medium,
        "notify_passengers": RiskTier.high,
    },
    warn_allowed_actions={"find_hotel_options": [CheckName.sources_fresh]},
)


# ------------------------------------------------------------------- 1. evidence_complete


class TestEvidenceComplete:
    def test_all_facts_present_passes(self):
        result = evidence_complete(
            required_facts=["event.type", "cause_evidence.external_to_carrier"],
            provided_facts={
                "event": {"type": "cancellation"},
                "cause_evidence": {"external_to_carrier": True},
            },
        )
        assert result.state is CheckState.passed
        assert result.reason_code is ReasonCode.OK

    def test_absent_key_fails(self):
        result = evidence_complete(
            required_facts=["flight.block_time_minutes"],
            provided_facts={"flight": {}},
        )
        assert result.state is CheckState.failed
        assert result.reason_code is ReasonCode.MISSING_REQUIRED_FACT
        assert result.reason == "flight.block_time_minutes is absent"

    def test_explicit_false_is_an_answer_but_none_is_not(self):
        """A null is not a legal answer. An explicit False is.

        This is the distinction the whole evidence model rests on: an airline asserting
        'no, this was avoidable' is an answer, while an empty field is not.
        """
        path = "cause_evidence.unavoidable_despite_reasonable_measures"

        answered = evidence_complete(
            required_facts=[path],
            provided_facts={"cause_evidence": {"unavoidable_despite_reasonable_measures": False}},
        )
        unanswered = evidence_complete(
            required_facts=[path],
            provided_facts={"cause_evidence": {"unavoidable_despite_reasonable_measures": None}},
        )

        assert answered.state is CheckState.passed
        assert unanswered.state is CheckState.failed
        assert unanswered.reason_code is ReasonCode.MISSING_REQUIRED_FACT

    def test_null_and_absent_are_indistinguishable_in_outcome(self):
        path = "cause_evidence.unavoidable_despite_reasonable_measures"
        explicit_null = evidence_complete(
            required_facts=[path],
            provided_facts={"cause_evidence": {"unavoidable_despite_reasonable_measures": None}},
        )
        absent = evidence_complete(required_facts=[path], provided_facts={"cause_evidence": {}})
        assert explicit_null.state is absent.state is CheckState.failed
        assert explicit_null.reason_code is absent.reason_code

    def test_zero_and_empty_string_are_answers(self):
        """0 is a real delay and "" is a real value; neither is absence."""
        result = evidence_complete(
            required_facts=["event.notice_minutes", "event.note"],
            provided_facts={"event": {"notice_minutes": 0, "note": ""}},
        )
        assert result.state is CheckState.passed

    def test_missing_facts_are_named_in_declaration_order(self):
        result = evidence_complete(
            required_facts=["fare.one_way_basic_fare_inr", "fare.airline_fuel_charge_inr"],
            provided_facts={},
        )
        assert result.reason is not None
        assert result.reason.index("one_way_basic_fare_inr") < result.reason.index(
            "airline_fuel_charge_inr"
        ), "declaration order must survive, so the gate and the engine agree"

    def test_no_required_facts_passes(self):
        assert evidence_complete(required_facts=[], provided_facts={}).state is CheckState.passed

    def test_path_through_a_non_mapping_is_absent_not_an_error(self):
        result = evidence_complete(
            required_facts=["flight.block_time_minutes"],
            provided_facts={"flight": "not-a-mapping"},
        )
        assert result.state is CheckState.failed


# ----------------------------------------------------------------------- 2. sources_fresh


class TestSourcesFresh:
    @pytest.mark.parametrize(
        ("age_minutes", "expected"),
        [(59, CheckState.passed), (60, CheckState.passed), (61, CheckState.failed)],
    )
    def test_freshness_boundary_at_the_configured_limit(self, age_minutes: int, expected):
        """metar_minutes is 60, so exactly 60 is fresh and 61 is not."""
        result = sources_fresh(
            sources={"metar:VOBL": NOW - timedelta(minutes=age_minutes)},
            now=NOW,
            config=CONFIG,
            action_type="notify_passengers",
        )
        assert result.state is expected

    def test_stale_source_without_permission_blocks(self):
        result = sources_fresh(
            sources={"metar:VOBL": NOW - timedelta(minutes=74)},
            now=NOW,
            config=CONFIG,
            action_type="notify_passengers",
        )
        assert result.state is CheckState.failed
        assert result.reason_code is ReasonCode.SOURCE_STALE
        assert result.evidence_refs == ["metar:VOBL"]

    def test_same_staleness_warns_for_a_permitted_action(self):
        """One config table, two outcomes. The action decides, not the source."""
        stale = {"metar:VABB": NOW - timedelta(minutes=71)}
        permitted = sources_fresh(
            sources=stale, now=NOW, config=CONFIG, action_type="find_hotel_options"
        )
        blocked = sources_fresh(
            sources=stale, now=NOW, config=CONFIG, action_type="notify_passengers"
        )
        assert permitted.state is CheckState.warn
        assert blocked.state is CheckState.failed
        assert permitted.reason_code is blocked.reason_code is ReasonCode.SOURCE_STALE

    def test_undated_source_is_not_downgraded_even_when_warn_is_permitted(self):
        """Unknown age is not known staleness, so the config tolerance does not apply."""
        result = sources_fresh(
            sources={"metar:VABB": None},
            now=NOW,
            config=CONFIG,
            action_type="find_hotel_options",
        )
        assert result.state is CheckState.failed
        assert result.reason_code is ReasonCode.SOURCE_MISSING_TIMESTAMP

    def test_naive_timestamp_fails_rather_than_being_guessed(self):
        result = sources_fresh(
            # Deliberately naive: an ambiguous timestamp is the subject of this test.
            sources={"metar:VOBL": datetime(2026, 8, 20, 15, 39)},
            now=NOW,
            config=CONFIG,
            action_type="find_hotel_options",
        )
        assert result.state is CheckState.failed
        assert result.reason_code is ReasonCode.SOURCE_MISSING_TIMESTAMP

    def test_future_dated_source_fails_rather_than_reading_as_fresh(self):
        result = sources_fresh(
            sources={"flight_status:AI2841": NOW + timedelta(minutes=30)},
            now=NOW,
            config=CONFIG,
            action_type="find_hotel_options",
        )
        assert result.state is CheckState.failed, "a broken feed must not read as maximally fresh"
        assert result.reason_code is ReasonCode.SOURCE_STALE

    def test_unknown_source_kind_fails_closed(self):
        """No configured freshness bound means nothing to check against."""
        result = sources_fresh(
            sources={"tarot:VOBL": NOW},
            now=NOW,
            config=CONFIG,
            action_type="find_hotel_options",
        )
        assert result.state is CheckState.failed
        assert result.reason_code is ReasonCode.CONFIG_MISSING

    def test_missing_timestamp_outranks_staleness_in_the_reported_code(self):
        result = sources_fresh(
            sources={"metar:VOBL": NOW - timedelta(minutes=90), "taf:VOBL": None},
            now=NOW,
            config=CONFIG,
            action_type="notify_passengers",
        )
        assert result.reason_code is ReasonCode.SOURCE_MISSING_TIMESTAMP

    def test_config_missing_outranks_every_other_source_problem(self):
        result = sources_fresh(
            sources={"tarot:VOBL": None, "metar:VOBL": None},
            now=NOW,
            config=CONFIG,
            action_type="notify_passengers",
        )
        assert result.reason_code is ReasonCode.CONFIG_MISSING

    def test_policy_pack_limit_is_read_in_days(self):
        """policy_pack_days is 3650, so a 2019 pack is still inside its bound in 2026."""
        result = sources_fresh(
            sources={"policy_pack:in-moca-charter-2019": NOW - timedelta(days=2757)},
            now=NOW,
            config=CONFIG,
            action_type="notify_passengers",
        )
        assert result.state is CheckState.passed

    def test_no_sources_passes(self):
        """Whether a source was required is evidence_complete's job, not this check's."""
        result = sources_fresh(sources={}, now=NOW, config=CONFIG, action_type="notify_passengers")
        assert result.state is CheckState.passed


# ----------------------------------------------------------------------- 3. entities_valid


class TestEntitiesValid:
    def test_all_resolved_passes(self):
        result = entities_valid(
            referenced_refs=["flight:1", "passenger:7"],
            resolved={"flight:1": {"id": 1}, "passenger:7": {"id": 7}},
        )
        assert result.state is CheckState.passed

    def test_absent_ref_fails(self):
        result = entities_valid(referenced_refs=["flight:9"], resolved={})
        assert result.state is CheckState.failed
        assert result.reason_code is ReasonCode.ENTITY_NOT_FOUND
        assert result.evidence_refs == ["flight:9"]

    @pytest.mark.parametrize("resolution", [None, False, {"exists": False}])
    def test_every_not_found_shape_fails(self, resolution):
        result = entities_valid(referenced_refs=["hotel:3"], resolved={"hotel:3": resolution})
        assert result.state is CheckState.failed
        assert result.reason_code is ReasonCode.ENTITY_NOT_FOUND

    def test_state_mismatch_fails_distinctly(self):
        result = entities_valid(
            referenced_refs=["flight:1"],
            resolved={"flight:1": {"exists": True, "state_matches": False}},
        )
        assert result.state is CheckState.failed
        assert result.reason_code is ReasonCode.ENTITY_STATE_MISMATCH

    def test_not_found_outranks_state_mismatch(self):
        result = entities_valid(
            referenced_refs=["flight:1", "crew:2"],
            resolved={"flight:1": {"state_matches": False}, "crew:2": None},
        )
        assert result.reason_code is ReasonCode.ENTITY_NOT_FOUND

    def test_no_references_passes(self):
        assert entities_valid(referenced_refs=[], resolved={}).state is CheckState.passed


# --------------------------------------------------------------------- 4. policy_compliant


class TestPolicyCompliant:
    def test_satisfied_constraints_pass(self):
        result = policy_compliant(
            action_type="reserve_hotel_block",
            payload={"rooms": 12, "rate_inr": 4200},
            constraints=[
                {"field": "rooms", "op": "lte", "value": 20},
                {"field": "rate_inr", "op": "lte", "value": 6000},
            ],
        )
        assert result.state is CheckState.passed

    def test_breach_fails(self):
        result = policy_compliant(
            action_type="reserve_hotel_block",
            payload={"rate_inr": 9000},
            constraints=[{"id": "hotel.max_rate", "field": "rate_inr", "op": "lte", "value": 6000}],
        )
        assert result.state is CheckState.failed
        assert result.reason_code is ReasonCode.POLICY_CONSTRAINT_BREACH
        assert result.reason is not None and "hotel.max_rate" in result.reason

    def test_boundary_value_is_compliant(self):
        result = policy_compliant(
            action_type="reserve_hotel_block",
            payload={"rate_inr": 6000},
            constraints=[{"field": "rate_inr", "op": "lte", "value": 6000}],
        )
        assert result.state is CheckState.passed

    def test_unknown_operator_fails_closed(self):
        """An unparseable constraint must never be silently skipped."""
        result = policy_compliant(
            action_type="reserve_hotel_block",
            payload={"rate_inr": 100},
            constraints=[{"field": "rate_inr", "op": "approximately", "value": 6000}],
        )
        assert result.state is CheckState.failed
        assert result.reason_code is ReasonCode.UNKNOWN_RULE_OPERATOR

    def test_unknown_operator_outranks_a_real_breach(self):
        result = policy_compliant(
            action_type="reserve_hotel_block",
            payload={"rate_inr": 9000},
            constraints=[
                {"field": "rate_inr", "op": "lte", "value": 6000},
                {"field": "rate_inr", "op": "vibes", "value": 1},
            ],
        )
        assert result.reason_code is ReasonCode.UNKNOWN_RULE_OPERATOR

    def test_missing_field_blocks_rather_than_assuming_compliance(self):
        result = policy_compliant(
            action_type="reserve_hotel_block",
            payload={},
            constraints=[{"field": "rate_inr", "op": "lte", "value": 6000}],
        )
        assert result.state is CheckState.failed
        assert result.reason_code is ReasonCode.MISSING_REQUIRED_FACT

    def test_null_field_blocks_like_a_missing_one(self):
        result = policy_compliant(
            action_type="reserve_hotel_block",
            payload={"rate_inr": None},
            constraints=[{"field": "rate_inr", "op": "lte", "value": 6000}],
        )
        assert result.reason_code is ReasonCode.MISSING_REQUIRED_FACT

    def test_unavailable_pack_is_never_compliant(self):
        result = policy_compliant(
            action_type="evaluate_entitlements",
            payload={},
            constraints=[{"id": "pack", "pack_unavailable": True, "reason": "hash mismatch"}],
        )
        assert result.state is CheckState.failed
        assert result.reason_code is ReasonCode.POLICY_PACK_UNAVAILABLE

    def test_constraint_scoped_to_other_actions_is_ignored(self):
        result = policy_compliant(
            action_type="check_connections",
            payload={"rate_inr": 99999},
            constraints=[
                {
                    "field": "rate_inr",
                    "op": "lte",
                    "value": 6000,
                    "applies_to_actions": ["reserve_hotel_block"],
                }
            ],
        )
        assert result.state is CheckState.passed

    def test_soft_breach_warns_and_still_has_no_route_to_execution(self):
        result = policy_compliant(
            action_type="reserve_hotel_block",
            payload={"rate_inr": 6100},
            constraints=[{"field": "rate_inr", "op": "lte", "value": 6000, "soft": True}],
        )
        assert result.state is CheckState.warn
        assert not CONFIG.warn_permitted("reserve_hotel_block", CheckName.policy_compliant), (
            "only sources_fresh has a configured route to execute_flagged"
        )

    def test_hard_breach_outranks_soft_breach(self):
        result = policy_compliant(
            action_type="reserve_hotel_block",
            payload={"rate_inr": 9000, "rooms": 40},
            constraints=[
                {"field": "rate_inr", "op": "lte", "value": 6000, "soft": True},
                {"field": "rooms", "op": "lte", "value": 20},
            ],
        )
        assert result.state is CheckState.failed

    def test_required_and_forbidden_presence_operators(self):
        assert (
            policy_compliant(
                action_type="notify_passengers",
                payload={"template_id": "delay_v2"},
                constraints=[{"field": "template_id", "op": "required"}],
            ).state
            is CheckState.passed
        )
        assert (
            policy_compliant(
                action_type="notify_passengers",
                payload={"raw_pii": "x"},
                constraints=[{"field": "raw_pii", "op": "forbidden"}],
            ).state
            is CheckState.failed
        )

    def test_max_total_sums_a_list(self):
        result = policy_compliant(
            action_type="reserve_hotel_block",
            payload={"amounts": [1000, 2000, 3000]},
            constraints=[{"field": "amounts", "op": "max_total", "value": 5000}],
        )
        assert result.state is CheckState.failed
        assert result.reason_code is ReasonCode.POLICY_CONSTRAINT_BREACH

    def test_incomparable_values_fail_rather_than_raise(self):
        result = policy_compliant(
            action_type="reserve_hotel_block",
            payload={"rate_inr": "six thousand"},
            constraints=[{"field": "rate_inr", "op": "lte", "value": 6000}],
        )
        assert result.state is CheckState.failed

    def test_no_constraints_passes(self):
        result = policy_compliant(action_type="record_outcome", payload={}, constraints=[])
        assert result.state is CheckState.passed


# ------------------------------------------------------------------------- 5. no_conflicts


class TestNoConflicts:
    def test_no_overlap_passes(self):
        result = no_conflicts(
            action_type="rebook_passengers",
            target_refs=["passenger:1"],
            pending_or_executed=[
                {
                    "action_type": "rebook_passengers",
                    "target_refs": ["passenger:2"],
                    "state": "succeeded",
                }
            ],
        )
        assert result.state is CheckState.passed

    def test_duplicate_action_fails(self):
        result = no_conflicts(
            action_type="rebook_passengers",
            target_refs=["passenger:1", "passenger:9"],
            pending_or_executed=[
                {
                    "action_type": "rebook_passengers",
                    "target_refs": ["passenger:1"],
                    "state": TaskState.succeeded.value,
                }
            ],
        )
        assert result.state is CheckState.failed
        assert result.reason_code is ReasonCode.DUPLICATE_ACTION
        assert result.evidence_refs == ["passenger:1"]

    def test_action_awaiting_approval_still_blocks_a_duplicate(self):
        result = no_conflicts(
            action_type="notify_passengers",
            target_refs=["incident:1"],
            pending_or_executed=[
                {
                    "action_type": "notify_passengers",
                    "target_refs": ["incident:1"],
                    "state": TaskState.needs_human.value,
                }
            ],
        )
        assert result.state is CheckState.failed

    @pytest.mark.parametrize(
        "state", [TaskState.failed.value, TaskState.rejected.value, TaskState.skipped.value]
    )
    def test_terminal_unsuccessful_attempts_do_not_block_a_retry(self, state: str):
        """One failed rebooking must not permanently wedge the passenger."""
        result = no_conflicts(
            action_type="rebook_passengers",
            target_refs=["passenger:1"],
            pending_or_executed=[
                {"action_type": "rebook_passengers", "target_refs": ["passenger:1"], "state": state}
            ],
        )
        assert result.state is CheckState.passed

    def test_unrecognised_state_blocks(self):
        result = no_conflicts(
            action_type="rebook_passengers",
            target_refs=["passenger:1"],
            pending_or_executed=[
                {
                    "action_type": "rebook_passengers",
                    "target_refs": ["passenger:1"],
                    "state": "half_done",
                }
            ],
        )
        assert result.state is CheckState.failed

    def test_unscoped_prior_action_is_treated_as_overlapping(self):
        result = no_conflicts(
            action_type="notify_passengers",
            target_refs=["incident:1"],
            pending_or_executed=[{"action_type": "notify_passengers", "state": "executing"}],
        )
        assert result.state is CheckState.failed, "an unscoped prior action cannot be ruled out"

    def test_exhausted_capacity_fails(self):
        result = no_conflicts(
            action_type="reserve_hotel_block",
            target_refs=["hotel:12"],
            pending_or_executed=[{"kind": "capacity", "ref": "hotel:12", "available": 0}],
        )
        assert result.state is CheckState.failed
        assert result.reason_code is ReasonCode.CAPACITY_UNAVAILABLE

    def test_unknown_capacity_is_not_spare_capacity(self):
        result = no_conflicts(
            action_type="reserve_hotel_block",
            target_refs=["hotel:12"],
            pending_or_executed=[{"kind": "capacity", "ref": "hotel:12", "available": None}],
        )
        assert result.state is CheckState.failed
        assert result.reason_code is ReasonCode.CAPACITY_UNAVAILABLE

    def test_available_capacity_passes(self):
        result = no_conflicts(
            action_type="reserve_hotel_block",
            target_refs=["hotel:12"],
            pending_or_executed=[{"kind": "capacity", "ref": "hotel:12", "available": 40}],
        )
        assert result.state is CheckState.passed

    def test_duplicate_outranks_capacity(self):
        result = no_conflicts(
            action_type="reserve_hotel_block",
            target_refs=["hotel:12"],
            pending_or_executed=[
                {"kind": "capacity", "ref": "hotel:12", "available": 0},
                {
                    "action_type": "reserve_hotel_block",
                    "target_refs": ["hotel:12"],
                    "state": "executing",
                },
            ],
        )
        assert result.reason_code is ReasonCode.DUPLICATE_ACTION

    def test_different_action_type_on_the_same_target_is_not_a_conflict(self):
        result = no_conflicts(
            action_type="check_connections",
            target_refs=["flight:1"],
            pending_or_executed=[
                {
                    "action_type": "assess_crew_impact",
                    "target_refs": ["flight:1"],
                    "state": "succeeded",
                }
            ],
        )
        assert result.state is CheckState.passed

    def test_empty_history_passes(self):
        result = no_conflicts(
            action_type="record_outcome", target_refs=["incident:1"], pending_or_executed=[]
        )
        assert result.state is CheckState.passed


# --------------------------------------------------------------------------- 6. action_risk


class TestActionRisk:
    @pytest.mark.parametrize(
        ("action", "tier"),
        [("check_connections", RiskTier.low), ("reserve_hotel_block", RiskTier.medium)],
    )
    def test_configured_low_and_medium_tiers(self, action: str, tier: RiskTier):
        result = action_risk(action_type=action, config=CONFIG)
        assert result.state is CheckState.passed
        assert result.tier is tier
        assert result.reason_code is ReasonCode.OK

    def test_high_risk_passes_the_check_while_demanding_a_human(self):
        """The classification succeeded. The action is still refused, by aggregation."""
        result = action_risk(action_type="notify_passengers", config=CONFIG)
        assert result.state is CheckState.passed
        assert result.tier is RiskTier.high
        assert result.reason_code is ReasonCode.HUMAN_APPROVAL_REQUIRED

    def test_unknown_action_type_is_high_risk(self):
        result = action_risk(action_type="wire_money", config=CONFIG)
        assert result.tier is RiskTier.high
        assert result.reason_code is ReasonCode.HUMAN_APPROVAL_REQUIRED
        assert result.reason is not None and "not in the configured risk tiers" in result.reason

    def test_this_check_never_fails(self):
        """It classifies; it does not judge. FAIL here would collapse the distinction."""
        for action in [*ActionType, "wire_money", ""]:
            value = action.value if isinstance(action, ActionType) else action
            assert action_risk(action_type=value, config=CONFIG).state is not CheckState.failed

    def test_every_real_action_type_is_classified(self):
        for action in ActionType:
            result = action_risk(action_type=action.value, config=CONFIG)
            assert result.tier in {RiskTier.low, RiskTier.medium, RiskTier.high}

    def test_empty_config_makes_everything_high(self):
        bare = AssuranceConfig(version="bare")
        for action in ActionType:
            assert action_risk(action_type=action.value, config=bare).tier is RiskTier.high
