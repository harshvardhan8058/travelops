"""Rule engine semantics, asserted exactly.

The pack cases in `test_pack_cases.py` are the reviewer-facing specification and are read with
containment semantics. These tests pin the behaviour precisely, including the cases the pack
only implies.

The one to read first is `TestWeatherNeverExemptsByItself`. If those assertions weaken, the
system has started inferring a legal conclusion from an operational trigger.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from app.config import PolicyMode
from app.policy.engine import (
    CODE_NO_CASH_RULE_MATCHED,
    ENGINE_VERSION,
    NOTICE_SUPERSESSION_SUSPECTED,
    OUTCOME_NEEDS_HUMAN,
    REASON_CONFLICTING_ENTITLEMENTS,
    REASON_DEFERS_TO_OTHER_JURISDICTION,
    REASON_MISSING_REQUIRED_FACT,
    REASON_UNKNOWN_RULE_OPERATOR,
    UNKNOWN,
    absent_facts,
    evaluate,
    evaluate_condition,
)
from app.policy.loader import load_pack

PACKS_ROOT = Path(__file__).resolve().parents[4] / "policy_packs"


@pytest.fixture(scope="module")
def pack():
    return load_pack(
        pack_dir=PACKS_ROOT,
        pack_id="in-moca-charter-2019",
        version="2019.02",
        mode=PolicyMode.charter,
    )


CANCELLATION_FACTS: dict[str, Any] = {
    "event": {"type": "cancellation", "notice_minutes": 600},
    "cancellation": {"notice_obligation_met": False},
    "flight": {"block_time_minutes": 95},
    "fare": {"one_way_basic_fare_inr": 4200, "airline_fuel_charge_inr": 800},
    "passenger": {"contact_info_provided_at_booking": True},
}


def _facts(**overrides: Any) -> dict[str, Any]:
    facts = copy.deepcopy(CANCELLATION_FACTS)
    for family, values in overrides.items():
        if values is None:
            facts.pop(family, None)
        else:
            facts.setdefault(family, {}).update(values)
    return facts


# ------------------------------------------------------------------- tri-state conditions


class TestTriStateConditions:
    def test_unknown_is_not_a_boolean(self):
        """A plain `if` on an unknown condition must explode rather than mean false."""
        with pytest.raises(TypeError):
            bool(UNKNOWN)

    def test_absent_fact_is_unknown_not_false(self):
        value, unknowns, _ = evaluate_condition({"fact": "a.b", "op": "eq", "value": 1}, {})
        assert value is UNKNOWN
        assert unknowns == ["a.b"]

    def test_null_fact_is_unknown(self):
        value, _, _ = evaluate_condition(
            {"fact": "a.b", "op": "eq", "value": 1}, {"a": {"b": None}}
        )
        assert value is UNKNOWN

    def test_explicit_false_is_a_real_answer(self):
        value, _, _ = evaluate_condition(
            {"fact": "a.b", "op": "eq", "value": True}, {"a": {"b": False}}
        )
        assert value is False

    @pytest.mark.parametrize("value", [False, None])
    def test_confirmation_requires_literal_true(self, value):
        outcome, unknowns, _ = evaluate_condition(
            {"fact": "review.confirmed", "op": "confirmed"},
            {"review": {"confirmed": value}},
        )
        assert outcome is UNKNOWN
        assert unknowns == ["review.confirmed"]

    def test_conjunction_is_false_when_one_conjunct_is_false(self):
        """This is what stops a cancellation rule blocking a delay evaluation."""
        node = {
            "all": [
                {"fact": "event.type", "op": "eq", "value": "cancellation"},
                {"fact": "flight.block_time_minutes", "op": "lte", "value": 60},
            ]
        }
        value, unknowns, _ = evaluate_condition(node, {"event": {"type": "delay"}})
        assert value is False
        assert unknowns == [], "a settled false needs no explanation from unknown siblings"

    def test_conjunction_is_unknown_when_nothing_is_false_but_something_is_absent(self):
        node = {
            "all": [
                {"fact": "event.type", "op": "eq", "value": "cancellation"},
                {"fact": "flight.block_time_minutes", "op": "lte", "value": 60},
            ]
        }
        value, unknowns, _ = evaluate_condition(node, {"event": {"type": "cancellation"}})
        assert value is UNKNOWN
        assert unknowns == ["flight.block_time_minutes"]

    def test_disjunction_is_true_when_one_disjunct_is_true(self):
        node = {
            "any_of": [{"fact": "a", "op": "eq", "value": 1}, {"fact": "b", "op": "eq", "value": 2}]
        }
        value, _, basis = evaluate_condition(node, {"b": 2})
        assert value is True
        assert basis == {"b": 2}

    def test_disjunction_is_unknown_when_none_true_and_one_absent(self):
        node = {
            "any_of": [{"fact": "a", "op": "eq", "value": 1}, {"fact": "b", "op": "eq", "value": 2}]
        }
        value, unknowns, _ = evaluate_condition(node, {"a": 9})
        assert value is UNKNOWN
        assert unknowns == ["b"]

    def test_absent_condition_is_vacuously_true(self):
        assert evaluate_condition(None, {})[0] is True

    @pytest.mark.parametrize(
        ("moment", "expected"),
        [
            ("21:10", True),
            ("20:00", True),
            ("03:00", True),
            ("11:05", False),
            ("19:59", False),
            ("02:59", True),
            ("03:01", False),
            ("2026-08-20T22:30:00+05:30", True),
        ],
    )
    def test_local_window_wraps_midnight(self, moment: str, expected: bool):
        node = {
            "fact": "flight.scheduled_departure_local_time",
            "op": "within_local_window",
            "value": {"from": "20:00", "to": "03:00"},
        }
        value, _, _ = evaluate_condition(
            node, {"flight": {"scheduled_departure_local_time": moment}}
        )
        assert value is expected

    def test_unparseable_time_is_unknown_not_outside_the_window(self):
        node = {
            "fact": "flight.scheduled_departure_local_time",
            "op": "within_local_window",
            "value": {"from": "20:00", "to": "03:00"},
        }
        value, _, _ = evaluate_condition(
            node, {"flight": {"scheduled_departure_local_time": "soon"}}
        )
        assert value is UNKNOWN

    def test_incomparable_values_are_unknown(self):
        value, _, _ = evaluate_condition(
            {"fact": "a", "op": "lt", "value": 5}, {"a": "not a number"}
        )
        assert value is UNKNOWN

    def test_absent_facts_reports_declaration_order(self):
        assert absent_facts(
            {}, ["fare.one_way_basic_fare_inr", "fare.airline_fuel_charge_inr"]
        ) == [
            "fare.one_way_basic_fare_inr",
            "fare.airline_fuel_charge_inr",
        ]


# --------------------------------------------------------------- the behaviour that matters


class TestWeatherNeverExemptsByItself:
    """A weather trigger is operational context, never a legal verdict."""

    def test_a_partially_evidenced_exemption_blocks(self, pack):
        """Cause asserted, decisive fact null. This must never auto-exempt."""
        result = evaluate(
            facts=_facts(
                cause_evidence={
                    "operational_cause": "meteorological",
                    "external_to_carrier": True,
                    "unavoidable_despite_reasonable_measures": None,
                }
            ),
            pack=pack,
        )
        assert result.outcome == OUTCOME_NEEDS_HUMAN
        assert result.blocking_reasons == [REASON_MISSING_REQUIRED_FACT]
        assert "cause_evidence.unavoidable_despite_reasonable_measures" in result.missing_facts
        assert result.cash_inr is None, "a blocked evaluation must not carry a figure"

    def test_trigger_type_alone_never_reaches_an_exemption(self, pack):
        """A trigger label with no cause evidence must not suppress anything.

        The entitlement stands at 5000: the exemption was never asserted, so it does not
        apply. If this ever returned 0, the engine would be reading a legal conclusion off an
        operational field.
        """
        result = evaluate(facts=_facts(event={"trigger_type": "weather"}), pack=pack)
        assert result.cash_inr == 5000
        assert "FORCE_MAJEURE" not in result.cash_reason_codes
        assert "exemption.force_majeure" not in result.rules_fired

    def test_a_fully_evidenced_exemption_suppresses_cash_but_not_care(self, pack):
        result = evaluate(
            facts=_facts(
                passenger={"reported_for_original_flight": True},
                alternate_flight={"offered": True},
                cause_evidence={
                    "operational_cause": "meteorological",
                    "external_to_carrier": True,
                    "unavoidable_despite_reasonable_measures": True,
                    "evidence_refs": ["metar:VOBL:2026-08-20T15:20Z"],
                },
            ),
            pack=pack,
        )
        assert result.cash_inr == 0
        assert "FORCE_MAJEURE" in result.cash_reason_codes
        assert "meals_refreshments" in result.entitlement_types, "duty of care survives"
        assert "cash" not in result.entitlement_types

    def test_an_exemption_refused_on_the_facts_does_not_block(self, pack):
        """external_to_carrier is False, so the exemption is settled, not unresolved."""
        result = evaluate(
            facts=_facts(
                cause_evidence={
                    "operational_cause": "crew_rostering",
                    "external_to_carrier": False,
                    "unavoidable_despite_reasonable_measures": False,
                }
            ),
            pack=pack,
        )
        assert result.outcome != OUTCOME_NEEDS_HUMAN
        assert result.cash_inr == 5000

    def test_an_exemption_claimed_without_evidence_refs_blocks(self, pack):
        """Both decisive facts asserted true, but nothing to cite. Not good enough."""
        result = evaluate(
            facts=_facts(
                cause_evidence={
                    "operational_cause": "meteorological",
                    "external_to_carrier": True,
                    "unavoidable_despite_reasonable_measures": True,
                }
            ),
            pack=pack,
        )
        assert result.outcome == OUTCOME_NEEDS_HUMAN
        assert "cause_evidence.evidence_refs" in result.missing_facts


class TestDelayNeverAttractsCash:
    def test_a_long_evening_weather_delay_pays_nothing(self, pack):
        result = evaluate(
            facts={
                "event": {
                    "type": "delay",
                    "delay_minutes": 420,
                    "expected_delay_minutes": 420,
                    "notice_minutes": 1500,
                },
                "itinerary": {"is_domestic": True},
                "flight": {"block_time_minutes": 165, "scheduled_departure_local_time": "21:10"},
                "passenger": {"checked_in_on_time": True},
            },
            pack=pack,
        )
        assert result.cash_inr == 0
        assert "NO_DELAY_COMPENSATION_PROVISION" in result.cash_reason_codes
        assert {"meals_refreshments", "hotel_accommodation", "passenger_choice"} <= set(
            result.entitlement_types
        )

    def test_no_delay_rule_in_the_pack_can_pay_cash(self, pack):
        """Structural, not scenario-based: no delay rule states a positive amount."""
        for rule in pack.evaluable_rules:
            when = str(rule.when)
            entitlement = rule.entitlement or {}
            if "'delay'" not in when:
                continue
            amount = entitlement.get("amount_inr")
            assert amount in (None, 0), f"{rule.id} would pay {amount} for a delay"
            assert not entitlement.get("formula"), f"{rule.id} computes cash for a delay"
            assert not entitlement.get("percentage"), f"{rule.id} computes cash for a delay"


class TestSupersededRules:
    def test_the_excluded_rule_never_evaluates_even_when_its_facts_match(self, pack):
        """Facts that would satisfy it exactly. It must still not fire."""
        result = evaluate(
            facts={
                "request": {"type": "cancellation", "minutes_after_booking": 600},
                "booking": {"days_before_first_leg": 20},
            },
            pack=pack,
        )
        assert "booking.free_cancel_or_amend_within_24h" not in result.rules_fired
        assert "booking.free_cancel_or_amend_within_24h" in result.excluded_rules
        notice = next(
            n
            for n in result.notices
            if n.get("rule_id") == "booking.free_cancel_or_amend_within_24h"
        )
        assert notice["notice"] == NOTICE_SUPERSESSION_SUSPECTED
        assert notice["evaluated"] is False
        assert notice["note"], "a supersession notice must explain itself"


class TestFailClosedOnMissingFacts:
    def test_missing_block_time_blocks_rather_than_guessing_a_band(self, pack):
        result = evaluate(facts=_facts(flight=None), pack=pack)
        assert result.outcome == OUTCOME_NEEDS_HUMAN
        assert result.missing_facts == ["flight.block_time_minutes"]
        assert result.cash_inr is None

    def test_missing_fare_components_block_the_computation(self, pack):
        result = evaluate(facts=_facts(fare=None), pack=pack)
        assert result.outcome == OUTCOME_NEEDS_HUMAN
        assert result.missing_facts == [
            "fare.one_way_basic_fare_inr",
            "fare.airline_fuel_charge_inr",
        ]

    def test_one_missing_fare_component_is_enough_to_block(self, pack):
        facts = _facts()
        facts["fare"].pop("airline_fuel_charge_inr")
        result = evaluate(facts=facts, pack=pack)
        assert result.outcome == OUTCOME_NEEDS_HUMAN
        assert result.missing_facts == ["fare.airline_fuel_charge_inr"]

    def test_a_null_fare_component_blocks_like_an_absent_one(self, pack):
        facts = _facts()
        facts["fare"]["airline_fuel_charge_inr"] = None
        result = evaluate(facts=facts, pack=pack)
        assert result.outcome == OUTCOME_NEEDS_HUMAN

    def test_a_zero_fare_component_is_a_real_value(self, pack):
        """0 is an answer. Only absence blocks."""
        facts = _facts()
        facts["fare"]["airline_fuel_charge_inr"] = 0
        result = evaluate(facts=facts, pack=pack)
        assert result.cash_inr == 4200

    def test_an_undetermined_rule_never_overrides_a_decided_one(self, pack):
        """Denied boarding with an alternate in 40 minutes.

        `no_alternate_taken` cannot be decided because the passenger's choice is unrecorded,
        but `alternate_within_1h` is definitively satisfied, so its figure stands and the
        undecided rule is reported alongside it rather than blocking.
        """
        result = evaluate(
            facts={
                "event": {"type": "denied_boarding"},
                "alternate_flight": {"minutes_after_original_scheduled": 40},
            },
            pack=pack,
        )
        assert result.cash_inr == 0
        assert result.outcome != OUTCOME_NEEDS_HUMAN
        undecided = [entry["rule_id"] for entry in result.undetermined_rules]
        assert "denied_boarding.no_alternate_taken" in undecided

    def test_undetermined_rules_are_always_reported(self, pack):
        result = evaluate(facts=_facts(), pack=pack)
        assert result.undetermined_rules, "an operator must see what could not be answered"
        for entry in result.undetermined_rules:
            assert entry["missing_facts"], "an undetermined rule must name its gap"


class TestOperatorAndConflictFailures:
    def test_an_unknown_operator_blocks(self, pack):
        broken = pack.model_copy(deep=True)
        broken.rules[0].when = {"all": [{"fact": "event.type", "op": "vibes", "value": "delay"}]}
        result = evaluate(facts={"event": {"type": "delay"}}, pack=broken)
        assert result.outcome == OUTCOME_NEEDS_HUMAN
        assert result.blocking_reasons == [REASON_UNKNOWN_RULE_OPERATOR]

    @staticmethod
    def _two_overlapping_cash_rules(pack):
        """Force the 7,500 and 10,000 bands to fire on the same facts."""
        rigged = pack.model_copy(deep=True)
        for rule in rigged.rules:
            if rule.id == "cancellation.compensation.block_over_120":
                rule.when = {"all": [{"fact": "event.type", "op": "eq", "value": "cancellation"}]}
        return rigged

    def test_conflicting_amounts_block_without_a_reviewed_precedence_rule(self, pack):
        """Two different figures and no reviewed way to choose is an open legal question.

        A fare high enough that both caps bind, so the bands disagree: 7,500 against 10,000.
        """
        facts = _facts(fare={"one_way_basic_fare_inr": 20000, "airline_fuel_charge_inr": 1000})
        result = evaluate(facts=facts, pack=self._two_overlapping_cash_rules(pack))
        assert result.outcome == OUTCOME_NEEDS_HUMAN
        assert result.blocking_reasons == [REASON_CONFLICTING_ENTITLEMENTS]
        assert result.cash_inr is None
        conflict = next(
            n for n in result.notices if n.get("notice") == REASON_CONFLICTING_ENTITLEMENTS
        )
        assert conflict["amounts"] == [7500, 10000]

    def test_two_rules_agreeing_on_the_same_figure_is_not_a_conflict(self, pack):
        """Only a disagreement is a conflict. Redundant agreement needs no human."""
        result = evaluate(facts=_facts(), pack=self._two_overlapping_cash_rules(pack))
        assert result.outcome != OUTCOME_NEEDS_HUMAN
        assert result.cash_inr == 5000

    def test_a_deferred_jurisdiction_blocks(self, pack):
        """A foreign carrier's country-of-origin rules are not this pack's to apply."""
        result = evaluate(
            facts=_facts(operating_carrier={"is_foreign": True, "country": "AE"}), pack=pack
        )
        assert result.outcome == OUTCOME_NEEDS_HUMAN
        assert result.blocking_reasons == [REASON_DEFERS_TO_OTHER_JURISDICTION]


class TestComputation:
    @pytest.mark.parametrize(
        ("block_time", "basic", "fuel", "expected", "cap"),
        [
            (55, 9000, 900, 5000, 5000),
            (60, 3000, 400, 3400, 5000),
            (95, 4200, 800, 5000, 7500),
            (120, 20000, 1000, 7500, 7500),
            (200, 14000, 1500, 10000, 10000),
        ],
    )
    def test_least_of_cap_and_fare_at_band_boundaries(
        self, pack, block_time: int, basic: int, fuel: int, expected: int, cap: int
    ):
        result = evaluate(
            facts=_facts(
                flight={"block_time_minutes": block_time},
                fare={"one_way_basic_fare_inr": basic, "airline_fuel_charge_inr": fuel},
            ),
            pack=pack,
        )
        assert result.cash_inr == expected
        assert f"cap {cap}" in (result.formula_used or "")

    def test_the_rendered_derivation_shows_its_working(self, pack):
        result = evaluate(facts=_facts(), pack=pack)
        assert result.formula_used == "least_of(cap 7500, basic_fare 4200 + fuel 800) = 5000"
        assert result.formula == "least_of_cap_and_basic_fare_plus_fuel_charge"

    @pytest.mark.parametrize(
        ("minutes", "basic", "fuel", "expected"),
        [(780, 3000, 500, 7000), (600, 8000, 1000, 10000), (1800, 8000, 1000, 20000)],
    )
    def test_denied_boarding_percentages(
        self, pack, minutes: int, basic: int, fuel: int, expected: int
    ):
        result = evaluate(
            facts={
                "event": {"type": "denied_boarding"},
                "alternate_flight": {"minutes_after_original_scheduled": minutes},
                "fare": {"one_way_basic_fare_inr": basic, "airline_fuel_charge_inr": fuel},
                "passenger": {"opted_for_alternate": True},
            },
            pack=pack,
        )
        assert result.cash_inr == expected

    def test_a_liability_ceiling_is_not_a_payout(self, pack):
        """A cap without a claim is a limit. Reporting it as cash would invent a claim."""
        result = evaluate(
            facts={
                "event": {"type": "baggage_loss_delay_damage"},
                "itinerary": {"is_domestic": False},
            },
            pack=pack,
        )
        assert result.cash_inr == 0
        assert CODE_NO_CASH_RULE_MATCHED in result.cash_reason_codes
        entitlement = next(item for item in result.entitlements if item.get("cap_sdr"))
        assert entitlement["cap_sdr"] == 1131
        assert entitlement["requires_currency_conversion"] is True
        assert entitlement["outcome"] == "limit"

    def test_no_matching_cash_rule_states_its_zero_rather_than_defaulting(self, pack):
        result = evaluate(
            facts=_facts(
                event={"notice_minutes": 30240}, cancellation={"notice_obligation_met": True}
            ),
            pack=pack,
        )
        assert result.cash_inr == 0
        assert CODE_NO_CASH_RULE_MATCHED in result.cash_reason_codes


class TestProvenance:
    def test_every_result_is_pinned_to_its_pack(self, pack):
        result = evaluate(facts=_facts(), pack=pack)
        assert result.pack_id == "in-moca-charter-2019"
        assert result.pack_version == "2019.02"
        assert result.pack_hash == pack.pack_hash
        assert result.pack_status == "official_guidance_dated"
        assert result.currency == "INR"
        assert result.engine_version == ENGINE_VERSION
        assert result.pack_ui_label == pack.ui_label

    def test_a_dated_pack_may_never_be_presented_as_current_law(self, pack):
        assert evaluate(facts=_facts(), pack=pack).may_be_presented_as_current_law is False

    def test_project_approval_without_verified_eligibility_is_not_current_law(self, pack):
        limited = pack.model_copy(
            update={
                "status": "approved",
                "verified_mode_eligible": False,
                "source_document_verified": True,
            }
        )
        result = evaluate(facts=_facts(), pack=limited)
        assert result.pack_status == "approved"
        assert result.verified_mode_eligible is False
        assert result.source_document_verified is True
        assert result.may_be_presented_as_current_law is False

    def test_every_fired_rule_contributes_its_clause_refs(self, pack):
        result = evaluate(facts=_facts(), pack=pack)
        assert result.source_clause_refs
        assert "charter:p3:flight-cancellation:scenario-2-B" in result.source_clause_refs
        for entitlement in result.entitlements:
            assert entitlement["source_clause_refs"], "a figure without a citation is unusable"

    def test_obligations_are_not_mistaken_for_entitlements(self, pack):
        """ "A credit shell must not be the default" is a prohibition, not something owed."""
        result = evaluate(facts=_facts(), pack=pack)
        assert any(item.get("forbids_default") == "credit_shell" for item in result.obligations)
        assert all(item.get("type") for item in result.entitlements)

    def test_the_same_facts_always_produce_the_same_result(self, pack):
        first = evaluate(facts=_facts(), pack=pack)
        second = evaluate(facts=_facts(), pack=pack)
        assert first.model_dump() == second.model_dump()
