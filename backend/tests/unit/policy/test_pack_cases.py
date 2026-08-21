"""The pack's own 23 cases, executed.

`policy_packs/in-moca-charter-2019/2019.02/test_cases.yaml` is both the engine's
specification and the SME review worksheet, so this harness runs every case in it and reports
by case id. A reviewer can read a failure name and go straight to the YAML.

## How each expectation is asserted

The cases are written by a rule author describing an outcome, not by a programmer describing a
return value, so the harness reads them with deliberate, documented semantics:

| Key | Assertion |
| --- | --- |
| `cash_inr` | exact, including `null` meaning "no figure was produced" |
| `decision` | exact match against the engine's outcome |
| `formula_used` | names a formula, matched against the named formula the pack selected |
| `entitlements` | expected types are a subset of those produced; `[]` means nothing non-cash owed |
| `must_not_include` | none of these types may appear — strict |
| `rules_fired` | expected ids are a subset of those that fired |
| `cash_reason_codes` | expected codes are a subset of those recorded |
| `missing_facts`, `blocking_reasons` | expected are a subset of those reported |
| `excluded_rules`, `surfaced_notice` | exact |
| `plus`, `cap_sdr`, `requires_currency_conversion` | read from the entitlement that carries them |
| `note`, `review_required`, `description` | documentation for the reviewer, nothing to assert |

Subset rather than equality for the "produced" lists, because the engine legitimately reports
more than the case author enumerated: a second independent reason for the same zero, or a rule
that also fired. Reporting more is not a weaker guarantee. Everything that constrains the
*number* — and everything expressed as a prohibition — is asserted strictly.

Beyond the per-case expectations, `TestInvariantsAcrossEveryCase` asserts properties that must
hold for all 23 regardless of what any case says, including the one that matters most: no
delay ever produces a cash payout.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.config import PolicyMode
from app.errors import PackNotVerifiedEligible, PolicyPackUnavailable
from app.policy.engine import (
    NOTICE_SUPERSESSION_SUSPECTED,
    OUTCOME_NEEDS_HUMAN,
    EntitlementResult,
    evaluate,
)
from app.policy.loader import LoadedPack, load_pack, load_test_cases

PACKS_ROOT = Path(__file__).resolve().parents[4] / "policy_packs"
PACK_ID = "in-moca-charter-2019"
PACK_VERSION = "2019.02"

CASES: list[dict[str, Any]] = load_test_cases(
    pack_dir=PACKS_ROOT, pack_id=PACK_ID, version=PACK_VERSION
)
CASE_IDS = [str(case["id"]) for case in CASES]

#: Entitlement types that represent money changing hands to the passenger.
CASH_TYPE = "cash"


@pytest.fixture(scope="module")
def pack() -> LoadedPack:
    return load_pack(
        pack_dir=PACKS_ROOT, pack_id=PACK_ID, version=PACK_VERSION, mode=PolicyMode.charter
    )


def _run(case: dict[str, Any], pack: LoadedPack) -> EntitlementResult:
    return evaluate(facts=case.get("facts") or {}, pack=pack)


def _owed_non_cash_types(result: EntitlementResult) -> set[str]:
    return {
        str(item.get("type"))
        for item in result.entitlements
        if item.get("type") and str(item.get("type")) != CASH_TYPE
    }


def _entitlement_value(result: EntitlementResult, key: str) -> Any:
    for item in result.entitlements:
        if key in item:
            return item[key]
    return None


def test_pack_declares_twenty_three_cases():
    """The definition of done is a count as well as a colour."""
    assert len(CASES) == 23, f"expected 23 pack cases, found {len(CASES)}"


class TestPackCases:
    """One test per case in the pack, named by case id."""

    @pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
    def test_case(self, case: dict[str, Any], pack: LoadedPack):
        expect = case.get("expect") or {}
        mode = case.get("mode")

        # A case that declares a mode is about loading, not evaluating.
        if mode is not None:
            self._assert_load_expectation(case, expect, mode)
            return

        result = _run(case, pack)

        if "decision" in expect:
            assert result.outcome == expect["decision"], (
                f"{case['id']}: expected outcome {expect['decision']}, got {result.outcome} "
                f"(blocking={result.blocking_reasons}, missing={result.missing_facts})"
            )

        if "cash_inr" in expect:
            assert result.cash_inr == expect["cash_inr"], (
                f"{case['id']}: expected cash_inr {expect['cash_inr']}, got {result.cash_inr}"
            )

        if "formula_used" in expect:
            assert result.formula == expect["formula_used"], (
                f"{case['id']}: expected formula {expect['formula_used']}, got {result.formula}"
            )
            assert result.formula_used, "a named formula must come with a rendered derivation"

        if "entitlements" in expect:
            expected_types = set(expect["entitlements"] or [])
            actual_types = set(result.entitlement_types)
            if expected_types:
                assert expected_types <= actual_types, (
                    f"{case['id']}: missing entitlements "
                    f"{sorted(expected_types - actual_types)} (got {sorted(actual_types)})"
                )
            else:
                assert not _owed_non_cash_types(result), (
                    f"{case['id']}: expected no entitlements, got "
                    f"{sorted(_owed_non_cash_types(result))}"
                )

        for forbidden in expect.get("must_not_include") or []:
            assert forbidden not in result.entitlement_types, (
                f"{case['id']}: {forbidden} must not be owed here"
            )

        for rule_id in expect.get("rules_fired") or []:
            assert rule_id in result.rules_fired, (
                f"{case['id']}: expected {rule_id} to fire, fired {result.rules_fired}"
            )
        if expect.get("rules_fired") == []:
            assert result.rules_fired == [], (
                f"{case['id']}: expected no rule to fire, fired {result.rules_fired}"
            )

        for code in expect.get("cash_reason_codes") or []:
            assert code in result.cash_reason_codes, (
                f"{case['id']}: expected reason {code}, got {result.cash_reason_codes}"
            )

        for reason in expect.get("blocking_reasons") or []:
            assert reason in result.blocking_reasons, (
                f"{case['id']}: expected blocking reason {reason}, got {result.blocking_reasons}"
            )

        for fact in expect.get("missing_facts") or []:
            assert fact in result.missing_facts, (
                f"{case['id']}: expected {fact} in missing_facts, got {result.missing_facts}"
            )

        if "excluded_rules" in expect:
            assert result.excluded_rules == list(expect["excluded_rules"])

        if "surfaced_notice" in expect:
            surfaced = [notice.get("notice") for notice in result.notices]
            assert expect["surfaced_notice"] in surfaced, (
                f"{case['id']}: expected notice {expect['surfaced_notice']}, got {surfaced}"
            )

        if "plus" in expect:
            assert _entitlement_value(result, "plus") == expect["plus"]

        if "cap_sdr" in expect:
            assert _entitlement_value(result, "cap_sdr") == expect["cap_sdr"]

        if "requires_currency_conversion" in expect:
            assert (
                _entitlement_value(result, "requires_currency_conversion")
                == expect["requires_currency_conversion"]
            )

    @staticmethod
    def _assert_load_expectation(case: dict[str, Any], expect: dict[str, Any], mode: str):
        policy_mode = PolicyMode(mode)
        if expect.get("load_result") != "rejected":
            load_pack(pack_dir=PACKS_ROOT, pack_id=PACK_ID, version=PACK_VERSION, mode=policy_mode)
            return

        with pytest.raises((PackNotVerifiedEligible, PolicyPackUnavailable)) as raised:
            load_pack(pack_dir=PACKS_ROOT, pack_id=PACK_ID, version=PACK_VERSION, mode=policy_mode)
        assert raised.value.code == expect["reason_code"], (
            f"{case['id']}: expected {expect['reason_code']}, got {raised.value.code}"
        )


class TestInvariantsAcrossEveryCase:
    """Properties that hold for all 23 cases whatever the individual case asserts."""

    @pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
    def test_delay_never_produces_a_cash_payout(self, case: dict[str, Any], pack: LoadedPack):
        """The strongest claim in the demo: this instrument has no delay compensation.

        Enforced here rather than in the engine, because "delay attracts no cash" is a reading
        of the source document and belongs in pack data. If a future pack legitimately adds a
        delay payout, this test fails loudly and a human decides — which is the correct
        failure mode for a legal change.
        """
        if case.get("mode") is not None:
            pytest.skip("load-time case, no evaluation")
        facts = case.get("facts") or {}
        if (facts.get("event") or {}).get("type") != "delay":
            pytest.skip("not a delay case")

        result = _run(case, pack)
        assert result.cash_inr in (0, None), (
            f"{case['id']}: a delay produced a payout of {result.cash_inr}"
        )

    @pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
    def test_the_superseded_rule_never_evaluates(self, case: dict[str, Any], pack: LoadedPack):
        if case.get("mode") is not None:
            pytest.skip("load-time case, no evaluation")
        result = _run(case, pack)
        assert "booking.free_cancel_or_amend_within_24h" not in result.rules_fired
        assert "booking.free_cancel_or_amend_within_24h" in result.excluded_rules
        assert NOTICE_SUPERSESSION_SUSPECTED in [n.get("notice") for n in result.notices]

    @pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
    def test_a_blocked_evaluation_never_carries_a_figure(
        self, case: dict[str, Any], pack: LoadedPack
    ):
        """needs_human with a number attached would read as a decision that nothing is owed."""
        if case.get("mode") is not None:
            pytest.skip("load-time case, no evaluation")
        result = _run(case, pack)
        if result.outcome == OUTCOME_NEEDS_HUMAN:
            assert result.cash_inr is None
            assert result.blocking_reasons, "a block must say why"

    @pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
    def test_every_result_is_pinned_to_the_pack_that_produced_it(
        self, case: dict[str, Any], pack: LoadedPack
    ):
        if case.get("mode") is not None:
            pytest.skip("load-time case, no evaluation")
        result = _run(case, pack)
        assert result.pack_id == PACK_ID
        assert result.pack_version == PACK_VERSION
        assert result.pack_hash == pack.pack_hash
        assert result.pack_status == "official_guidance_dated"
        assert not result.may_be_presented_as_current_law

    @pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
    def test_every_fired_rule_carries_a_citation(self, case: dict[str, Any], pack: LoadedPack):
        """A figure without a clause reference is not something we would show anyone."""
        if case.get("mode") is not None:
            pytest.skip("load-time case, no evaluation")
        result = _run(case, pack)
        for rule_id in result.rules_fired:
            rule = pack.rule(rule_id)
            assert rule is not None and rule.source_clause_refs, (
                f"{case['id']}: {rule_id} fired without a source clause reference"
            )

    @pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
    def test_evaluation_is_reproducible(self, case: dict[str, Any], pack: LoadedPack):
        if case.get("mode") is not None:
            pytest.skip("load-time case, no evaluation")
        assert _run(case, pack).model_dump() == _run(case, pack).model_dump()
