"""The callable Stream C's compensation service uses.

The contract these tests defend: a caller gets a number only together with the formula, the
clause references and the pack version, so nothing downstream can render a bare figure or
present a dated one as current law.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.config import PolicyMode, Settings
from app.errors import PackNotVerifiedEligible
from app.policy.entitlements import (
    REASON_APPLICABILITY_UNRESOLVED,
    CitedEntitlement,
    calculate,
    load_active_pack,
)
from app.policy.loader import load_pack

PACKS_ROOT = Path(__file__).resolve().parents[4] / "policy_packs"


def _settings(**overrides: Any) -> Settings:
    defaults = {
        "policy_pack_dir": PACKS_ROOT,
        "policy_pack_id": "in-moca-charter-2019",
        "policy_pack_version": "2019.02",
        "policy_mode": "charter",
    }
    return Settings(_env_file=None, **{**defaults, **overrides})


@pytest.fixture(scope="module")
def pack():
    return load_pack(
        pack_dir=PACKS_ROOT,
        pack_id="in-moca-charter-2019",
        version="2019.02",
        mode=PolicyMode.charter,
    )


#: A complete cancellation, including the applicability facts the resolver needs.
FULL_FACTS: dict[str, Any] = {
    "itinerary": {
        "origin_country": "IN",
        "destination_country": "IN",
        "scheduled_departure_local": "2026-08-20T21:10:00+05:30",
        "is_domestic": True,
    },
    "operating_carrier": {"id": "AI", "country": "IN"},
    "event": {"type": "cancellation", "notice_minutes": 600, "travel_date": "2026-08-20"},
    "cancellation": {"notice_obligation_met": False},
    "flight": {"block_time_minutes": 95},
    "fare": {"one_way_basic_fare_inr": 4200, "airline_fuel_charge_inr": 800},
    "passenger": {"contact_info_provided_at_booking": True},
}


class TestTheNumberArrivesWithItsDerivation:
    def test_a_figure_is_never_returned_alone(self, pack):
        result = calculate(facts=FULL_FACTS, pack=pack, settings=_settings())

        assert result.cash_inr == 5000
        assert result.formula == "least_of_cap_and_basic_fare_plus_fuel_charge"
        assert result.formula_used == "least_of(cap 7500, basic_fare 4200 + fuel 800) = 5000"
        assert result.source_clause_refs
        assert result.pack_version == "2019.02"
        assert result.pack_hash == pack.pack_hash
        assert result.currency == "INR"
        assert result.has_citation

    def test_the_derivation_is_renderable_as_the_ui_expects(self, pack):
        """The UI shows the working, not a bare figure."""
        result = calculate(facts=FULL_FACTS, pack=pack, settings=_settings())
        assert result.formula_used is not None
        for fragment in ("cap 7500", "4200", "800", "5000"):
            assert fragment in result.formula_used

    def test_the_badge_cannot_claim_current_law(self, pack):
        result = calculate(facts=FULL_FACTS, pack=pack, settings=_settings())
        assert result.pack_status == "official_guidance_dated"
        assert result.may_be_presented_as_current_law is False
        assert result.pack_ui_label == pack.ui_label
        assert result.policy_mode == "charter"

    def test_project_approval_without_verified_eligibility_stays_non_current(self, pack):
        limited = pack.model_copy(
            update={
                "status": "approved",
                "verified_mode_eligible": False,
                "source_document_verified": True,
            }
        )
        result = calculate(
            facts=FULL_FACTS,
            pack=limited,
            settings=_settings(),
            resolve_applicability=False,
        )
        assert result.pack_status == "approved"
        assert result.verified_mode_eligible is False
        assert result.source_document_verified is True
        assert result.may_be_presented_as_current_law is False

    def test_applicability_is_reported_alongside_the_figure(self, pack):
        result = calculate(facts=FULL_FACTS, pack=pack, settings=_settings())
        assert result.applicability
        assert result.applicability[0]["status"] == "applicable"
        assert result.resolver_version == "resolver-v1"


class TestBlockingIsAnOutcomeNotAnException:
    def test_a_missing_fact_returns_needs_human_rather_than_raising(self, pack):
        facts = {**FULL_FACTS, "fare": {}}
        result = calculate(facts=facts, pack=pack, settings=_settings())
        assert result.requires_human
        assert result.cash_inr is None
        assert result.missing_facts == [
            "fare.one_way_basic_fare_inr",
            "fare.airline_fuel_charge_inr",
        ]

    def test_unresolved_applicability_blocks_before_any_rule_runs(self, pack):
        facts = {key: value for key, value in FULL_FACTS.items() if key != "operating_carrier"}
        result = calculate(facts=facts, pack=pack, settings=_settings())
        assert result.requires_human
        assert REASON_APPLICABILITY_UNRESOLVED in result.blocking_reasons
        assert result.rules_fired == []
        assert result.cash_inr is None

    def test_a_partially_evidenced_exemption_blocks(self, pack):
        facts = {
            **FULL_FACTS,
            "cause_evidence": {
                "operational_cause": "meteorological",
                "external_to_carrier": True,
                "unavoidable_despite_reasonable_measures": None,
            },
        }
        result = calculate(facts=facts, pack=pack, settings=_settings())
        assert result.requires_human
        assert "cause_evidence.unavoidable_despite_reasonable_measures" in result.missing_facts

    def test_resolution_can_be_skipped_when_the_caller_already_resolved(self, pack):
        """Evaluating a pre-selected pack must not re-demand the applicability facts."""
        facts = {key: value for key, value in FULL_FACTS.items() if key != "operating_carrier"}
        result = calculate(
            facts=facts, pack=pack, settings=_settings(), resolve_applicability=False
        )
        assert result.cash_inr == 5000


class TestModeEnforcement:
    def test_verified_mode_cannot_load_the_pack(self):
        with pytest.raises(PackNotVerifiedEligible):
            load_active_pack(_settings(policy_mode="verified"))

    def test_a_delay_never_returns_cash_through_this_api(self, pack):
        facts = {
            **FULL_FACTS,
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
        result = calculate(facts=facts, pack=pack, settings=_settings())
        assert result.cash_inr == 0
        assert "NO_DELAY_COMPENSATION_PROVISION" in result.cash_reason_codes
        assert {"meals_refreshments", "hotel_accommodation"} <= {
            item["type"] for item in result.entitlements
        }

    def test_the_superseded_rule_is_reported_but_never_fired(self, pack):
        result = calculate(facts=FULL_FACTS, pack=pack, settings=_settings())
        assert "booking.free_cancel_or_amend_within_24h" in result.excluded_rules
        assert "booking.free_cancel_or_amend_within_24h" not in result.rules_fired


class TestContractShape:
    def test_the_response_rejects_unknown_fields(self):
        """Nothing downstream can bolt a confidence score onto a legal figure."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            CitedEntitlement(outcome="evaluated", policy_mode="charter", confidence=91)

    def test_it_serialises_for_the_api_and_the_audit_record(self, pack):
        payload = calculate(facts=FULL_FACTS, pack=pack, settings=_settings()).model_dump(
            mode="json"
        )
        for key in (
            "cash_inr",
            "formula",
            "formula_used",
            "source_clause_refs",
            "pack_version",
            "pack_hash",
            "pack_status",
            "policy_mode",
        ):
            assert key in payload

    def test_repeated_calls_agree(self, pack):
        first = calculate(facts=FULL_FACTS, pack=pack, settings=_settings())
        second = calculate(facts=FULL_FACTS, pack=pack, settings=_settings())
        assert first.model_dump() == second.model_dump()
