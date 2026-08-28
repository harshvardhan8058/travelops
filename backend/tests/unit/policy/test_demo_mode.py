"""G8 — POLICY_MODE=demo.

Before this, demo mode could load nothing: the loader accepts only a pack marked
`demo_fixture: true` and no pack was, so demo mode failed on every path. The fix is a fictional
pack plus one authoritative place where POLICY_MODE resolves to a pack.

The two properties that matter are opposites of each other, and both are asserted here: demo mode
**must** load the fictional pack, and it must be **unable** to load a real one. A demo mode that
could be pointed at the charter pack by an environment variable would put real figures behind a
badge that says the numbers are invented.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import PolicyMode, Settings
from app.errors import PackNotVerifiedEligible, PolicyPackUnavailable
from app.models.enums import PolicyPackStatus
from app.policy.entitlements import (
    DEMO_PACK_ID,
    DEMO_PACK_VERSION,
    active_pack_coordinates,
    calculate,
    load_active_pack,
)
from app.policy.loader import load_pack, load_test_cases

PACKS_ROOT = Path(__file__).resolve().parents[4] / "policy_packs"
CHARTER_ID = "in-moca-charter-2019"


def _settings(mode: str = "demo", **overrides) -> Settings:
    return Settings(_env_file=None, policy_pack_dir=PACKS_ROOT, policy_mode=mode, **overrides)


@pytest.fixture(scope="module")
def demo_pack():
    return load_pack(
        pack_dir=PACKS_ROOT, pack_id=DEMO_PACK_ID, version=DEMO_PACK_VERSION, mode=PolicyMode.demo
    )


class TestDemoModeLoads:
    def test_demo_mode_now_loads_a_pack_at_all(self):
        """The G8 defect: demo mode previously had nothing it could load."""
        pack = load_active_pack(_settings("demo"))
        assert pack.pack_id == DEMO_PACK_ID
        assert pack.version == DEMO_PACK_VERSION

    def test_demo_mode_ignores_a_configured_real_pack(self):
        """One wrong environment variable must not put real figures behind the demo badge."""
        settings = _settings("demo", policy_pack_id=CHARTER_ID, policy_pack_version="2019.02")
        assert active_pack_coordinates(settings) == (DEMO_PACK_ID, DEMO_PACK_VERSION)
        assert load_active_pack(settings).pack_id == DEMO_PACK_ID

    def test_charter_and_verified_modes_still_honour_the_configured_pack(self):
        charter = _settings("charter")
        assert active_pack_coordinates(charter) == (
            charter.policy_pack_id,
            charter.policy_pack_version,
        )

    def test_the_engine_runs_end_to_end_in_demo_mode(self, demo_pack):
        """Demo mode exists to prove the engine, so it has to actually compute something."""
        result = calculate(
            facts={
                "itinerary": {"origin_country": "XX", "destination_country": "XX"},
                "operating_carrier": {"id": "XX1"},
                "event": {"type": "cancellation", "notice_minutes": 600},
                "fare": {"one_way_basic_fare_inr": 1800, "airline_fuel_charge_inr": 400},
            },
            pack=demo_pack,
            settings=_settings("demo"),
        )
        assert result.cash_inr == 2200
        assert result.policy_mode == "demo"


class TestDemoModeClaimsNothing:
    def test_the_pack_is_fictional_and_cites_nothing(self, demo_pack):
        assert demo_pack.demo_fixture is True
        assert demo_pack.citations_permitted is False
        assert all(not rule.source_clause_refs for rule in demo_pack.rules)

    def test_it_creates_no_regulatory_authority(self, demo_pack):
        assert demo_pack.jurisdiction == "XX"
        assert "does not exist" in demo_pack.authority
        assert demo_pack.status is PolicyPackStatus.draft
        assert demo_pack.verified_mode_eligible is False
        assert demo_pack.may_be_called_current_law is False

    def test_the_badge_says_it_is_not_law(self, demo_pack):
        label = demo_pack.ui_label.upper()
        assert "DEMO" in label
        assert "NOT LAW" in label

    def test_it_carries_no_source_document_and_claims_no_hash(self, demo_pack):
        """Inventing a hash would make the integrity check pass on a document nobody holds."""
        assert demo_pack.source_archived is False
        assert demo_pack.source_content_sha256 is None
        assert demo_pack.source_document_verified is False

    def test_a_computed_figure_is_never_presentable_as_current_law(self, demo_pack):
        result = calculate(
            facts={
                "itinerary": {"origin_country": "XX", "destination_country": "XX"},
                "operating_carrier": {"id": "XX1"},
                "event": {"type": "cancellation", "notice_minutes": 600},
                "fare": {"one_way_basic_fare_inr": 1800, "airline_fuel_charge_inr": 400},
            },
            pack=demo_pack,
            settings=_settings("demo"),
        )
        assert result.may_be_presented_as_current_law is False
        assert result.pack_status == "draft"
        assert result.has_citation is False


class TestDemoIsDistinctFromCharterAndVerified:
    def test_the_fictional_pack_cannot_load_in_charter_mode(self):
        """Charter mode loads dated official guidance, which this is not."""
        with pytest.raises(PolicyPackUnavailable):
            load_pack(
                pack_dir=PACKS_ROOT,
                pack_id=DEMO_PACK_ID,
                version=DEMO_PACK_VERSION,
                mode=PolicyMode.charter,
            )

    def test_the_fictional_pack_cannot_load_in_verified_mode(self):
        with pytest.raises(PackNotVerifiedEligible) as raised:
            load_pack(
                pack_dir=PACKS_ROOT,
                pack_id=DEMO_PACK_ID,
                version=DEMO_PACK_VERSION,
                mode=PolicyMode.verified,
            )
        assert raised.value.code == "PACK_NOT_VERIFIED_ELIGIBLE"

    def test_the_charter_pack_cannot_load_in_demo_mode(self):
        """Unchanged, and the reason demo mode needed its own pack."""
        with pytest.raises(PolicyPackUnavailable):
            load_pack(
                pack_dir=PACKS_ROOT,
                pack_id=CHARTER_ID,
                version="2019.02",
                mode=PolicyMode.demo,
            )

    def test_the_three_modes_differ_in_what_they_permit(self, demo_pack):
        charter = load_pack(
            pack_dir=PACKS_ROOT,
            pack_id=CHARTER_ID,
            version="2019.02",
            mode=PolicyMode.charter,
        )
        # demo computes but cites nothing; charter computes and cites; neither is current law.
        assert (demo_pack.citations_permitted, charter.citations_permitted) == (False, True)
        assert demo_pack.may_be_called_current_law is charter.may_be_called_current_law is False
        assert demo_pack.status is not charter.status

    def test_verified_fail_closed_behaviour_is_untouched(self):
        """G8 must not become a way in to verified mode."""
        from app.config import ConfigurationError, resolve_modes

        with pytest.raises(ConfigurationError, match="PACK_NOT_VERIFIED_ELIGIBLE"):
            resolve_modes(Settings(_env_file=None, policy_mode="verified"))


class TestDemoPackCases:
    """The fictional pack is held to its own executable expectations."""

    CASES = load_test_cases(pack_dir=PACKS_ROOT, pack_id=DEMO_PACK_ID, version=DEMO_PACK_VERSION)

    def test_the_pack_declares_cases(self):
        assert len(self.CASES) == 11

    @pytest.mark.parametrize("case", CASES, ids=[str(c["id"]) for c in CASES])
    def test_case(self, case: dict, demo_pack):
        from app.policy.engine import evaluate

        expect = case.get("expect") or {}
        mode = case.get("mode")

        if mode is not None:
            with pytest.raises((PackNotVerifiedEligible, PolicyPackUnavailable)) as raised:
                load_pack(
                    pack_dir=PACKS_ROOT,
                    pack_id=DEMO_PACK_ID,
                    version=DEMO_PACK_VERSION,
                    mode=PolicyMode(mode),
                )
            assert raised.value.code == expect["reason_code"]
            return

        result = evaluate(facts=case.get("facts") or {}, pack=demo_pack)

        if "decision" in expect:
            assert result.outcome == expect["decision"]
        if "cash_inr" in expect:
            assert result.cash_inr == expect["cash_inr"]
        if "formula_used" in expect:
            assert result.formula == expect["formula_used"]
        for code in expect.get("cash_reason_codes") or []:
            assert code in result.cash_reason_codes
        for rule_id in expect.get("rules_fired") or []:
            assert rule_id in result.rules_fired
        for fact in expect.get("missing_facts") or []:
            assert fact in result.missing_facts
        for reason in expect.get("blocking_reasons") or []:
            assert reason in result.blocking_reasons
        if "excluded_rules" in expect:
            assert result.excluded_rules == list(expect["excluded_rules"])
        if "surfaced_notice" in expect:
            assert expect["surfaced_notice"] in [n.get("notice") for n in result.notices]
        if "plus" in expect:
            assert any(item.get("plus") == expect["plus"] for item in result.entitlements)
        if "entitlements" in expect:
            expected = set(expect["entitlements"] or [])
            if expected:
                assert expected <= set(result.entitlement_types)
            else:
                assert not [
                    item
                    for item in result.entitlements
                    if item.get("type") and item["type"] != "cash"
                ]

    @pytest.mark.parametrize("case", CASES, ids=[str(c["id"]) for c in CASES])
    def test_no_case_ever_produces_a_citation(self, case: dict, demo_pack):
        """Whatever the fictional rules do, nothing may present a source reference."""
        from app.policy.engine import evaluate

        if case.get("mode") is not None:
            pytest.skip("load-time case")
        result = evaluate(facts=case.get("facts") or {}, pack=demo_pack)
        assert result.source_clause_refs == []
        assert result.may_be_presented_as_current_law is False
