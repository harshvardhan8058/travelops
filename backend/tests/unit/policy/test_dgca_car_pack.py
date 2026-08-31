"""G1 — the DGCA CAR primary-source pack.

Built from two official documents: CAR Section 3 Series 'M' Part IV Rev. 4 (25 Jan 2023,
effective 15 Feb 2023) and, because Part IV Para 3.3.5 defers ticket refund to it, CAR Section 3
Series 'M' Part II Rev. 3 (24 Feb 2026, effective 26 Mar 2026).

Three groups of assertion:

  * the pack's own 32 executable cases, run through the real engine;
  * the provenance guards — both archived original PDFs must still hash to the values recorded in
    `source-metadata.yaml`, so those hashes are checked rather than decorative;
  * the approval guards — valid source integrity must NOT promote this pack: it remains unavailable
    in verified mode, must not claim current law, and carries no reviewer or approval because none
    exists. If someone fills those in without an SME, these tests are what should stop them.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.config import PolicyMode
from app.errors import PackNotVerifiedEligible, PolicyPackUnavailable
from app.models.enums import PolicyPackStatus
from app.policy.engine import evaluate
from app.policy.loader import load_pack, load_test_cases

PACKS_ROOT = Path(__file__).resolve().parents[4] / "policy_packs"
PACK_ID = "in-dgca-car-3m4"
PACK_VERSION = "rev4-2023.01"
PACK_DIR = PACKS_ROOT / PACK_ID / PACK_VERSION

CASES: list[dict[str, Any]] = load_test_cases(
    pack_dir=PACKS_ROOT, pack_id=PACK_ID, version=PACK_VERSION
)
CASE_IDS = [str(case["id"]) for case in CASES]


@pytest.fixture(scope="module")
def pack():
    return load_pack(
        pack_dir=PACKS_ROOT, pack_id=PACK_ID, version=PACK_VERSION, mode=PolicyMode.charter
    )


@pytest.fixture(scope="module")
def source_metadata() -> dict[str, Any]:
    return yaml.safe_load((PACK_DIR / "source-metadata.yaml").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ the pack's own cases


class TestPackCases:
    def test_the_pack_declares_its_cases(self):
        assert len(CASES) == 32

    @pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
    def test_case(self, case: dict[str, Any], pack):
        expect = case.get("expect") or {}
        mode = case.get("mode")

        if mode is not None:
            with pytest.raises((PackNotVerifiedEligible, PolicyPackUnavailable)) as raised:
                load_pack(
                    pack_dir=PACKS_ROOT,
                    pack_id=PACK_ID,
                    version=PACK_VERSION,
                    mode=PolicyMode(mode),
                )
            assert raised.value.code == expect["reason_code"]
            return

        result = evaluate(facts=case.get("facts") or {}, pack=pack)

        if "decision" in expect:
            assert result.outcome == expect["decision"], (
                f"{case['id']}: blocking={result.blocking_reasons} missing={result.missing_facts}"
            )
        if "cash_inr" in expect:
            assert result.cash_inr == expect["cash_inr"], case["id"]
        if "formula_used" in expect:
            assert result.formula == expect["formula_used"]
        if "plus" in expect:
            assert any(item.get("plus") == expect["plus"] for item in result.entitlements)

        for rule_id in expect.get("rules_fired") or []:
            assert rule_id in result.rules_fired, f"{case['id']}: {rule_id} did not fire"
        for rule_id in expect.get("rules_not_fired") or []:
            assert rule_id not in result.rules_fired, f"{case['id']}: {rule_id} fired"
        for entitlement in expect.get("entitlements") or []:
            assert entitlement in result.entitlement_types, f"{case['id']}: {entitlement}"
        for forbidden in expect.get("must_not_include") or []:
            assert forbidden not in result.entitlement_types, f"{case['id']}: {forbidden}"
        for fact in expect.get("missing_facts") or []:
            assert fact in result.missing_facts, f"{case['id']}: {fact}"
        for code in expect.get("cash_reason_codes") or []:
            assert code in result.cash_reason_codes, f"{case['id']}: {code}"

    @pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
    def test_every_fired_rule_cites_a_clause(self, case: dict[str, Any], pack):
        """A figure without a clause reference is not traceable to the regulation."""
        if case.get("mode") is not None:
            pytest.skip("load-time case")
        result = evaluate(facts=case.get("facts") or {}, pack=pack)
        for rule_id in result.rules_fired:
            rule = pack.rule(rule_id)
            assert rule is not None and rule.source_clause_refs, rule_id

    @pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
    def test_a_delay_never_produces_cash(self, case: dict[str, Any], pack):
        """Para 3.4 contains no monetary compensation. Cash is 3.2.2, 3.3.2 and 3.5.1 only."""
        if case.get("mode") is not None:
            pytest.skip("load-time case")
        facts = case.get("facts") or {}
        if (facts.get("event") or {}).get("type") != "delay":
            pytest.skip("not a delay case")
        result = evaluate(facts=facts, pack=pack)
        assert result.cash_inr in (0, None), case["id"]

    @pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
    def test_a_blocked_evaluation_carries_no_figure(self, case: dict[str, Any], pack):
        if case.get("mode") is not None:
            pytest.skip("load-time case")
        result = evaluate(facts=case.get("facts") or {}, pack=pack)
        if result.outcome == "needs_human":
            assert result.cash_inr is None
            assert result.blocking_reasons


# ---------------------------------------------------------------------------- provenance


class TestSourceProvenance:
    def test_part_iv_identity_matches_the_document(self, source_metadata):
        assert source_metadata["series"] == "Series 'M' Part IV"
        assert source_metadata["issue"] == "Issue I"
        assert str(source_metadata["issue_date"]) == "2010-08-06"
        assert source_metadata["revision"] == "Rev. 4"
        assert str(source_metadata["revision_date"]) == "2023-01-25"
        assert str(source_metadata["effective_date"]) == "2023-02-15"
        assert source_metadata["file_number"] == "23-15/2016-AED"

    def test_part_ii_identity_matches_the_document(self, source_metadata):
        [part_ii] = source_metadata["referenced_instruments"]
        assert part_ii["series"] == "Series 'M' Part II"
        assert part_ii["revision"] == "Rev. 3"
        assert str(part_ii["revision_date"]) == "2026-02-24"
        assert str(part_ii["effective_date"]) == "2026-03-26"
        assert part_ii["file_number"] == "23-16/2016-AED"
        assert part_ii["referenced_by"] == "car:3m4:rev4:3.3.5"

    @pytest.mark.parametrize(
        ("metadata_key", "expected_sha256", "expected_size"),
        [
            (
                None,
                "3b4b50844edc6a46099cce3b94626d29b03f90ccc61318aa1aa2db6d0fa3ff4a",
                285108,
            ),
            (
                "part_ii",
                "558ecee1d535b63023dd8bd37f0de10d08e7eb82a37ec48817ec1d613ff09281",
                2754578,
            ),
        ],
    )
    def test_archived_pdf_matches_its_recorded_hash(
        self, source_metadata, metadata_key, expected_sha256, expected_size
    ):
        """Both digests are recomputed from the archived PDF bytes on every run."""
        metadata = source_metadata
        if metadata_key == "part_ii":
            [metadata] = source_metadata["referenced_instruments"]

        archived = PACK_DIR / metadata["local_path"]
        assert archived.is_file(), "the source PDF must be archived in the pack"
        assert archived.read_bytes()[:5] == b"%PDF-"

        digest = hashlib.sha256(archived.read_bytes()).hexdigest()
        assert digest == metadata["content_sha256"] == expected_sha256
        assert archived.stat().st_size == metadata["size_bytes"] == expected_size
        assert metadata["archived"] is True

    def test_archived_pdfs_are_exact_copies_of_the_repository_originals(self, source_metadata):
        [part_ii] = source_metadata["referenced_instruments"]
        pairs = [
            (source_metadata, PACKS_ROOT / "D3M-M4(R4_Jan2023).pdf"),
            (part_ii, PACKS_ROOT / "CAR 3M2 Feb26.pdf"),
        ]
        for metadata, original in pairs:
            archived = PACK_DIR / metadata["local_path"]
            assert original.is_file()
            assert archived.read_bytes() == original.read_bytes()

    def test_primary_source_integrity_passes_without_promoting_the_pack(
        self, source_metadata, pack
    ):
        assert source_metadata["archived"] is True
        assert pack.source_document_verified is True
        assert pack.source_integrity_reason is None
        assert pack.status is PolicyPackStatus.official_guidance_dated
        assert pack.verified_mode_eligible is False

    def test_a_supersession_check_is_recorded_for_both_documents(self, source_metadata):
        check = source_metadata["supersession_check"]
        assert check["part_iv"]["evidence"]
        assert check["part_ii"]["evidence"]
        assert check["caveat"]

    def test_redistribution_basis_is_recorded(self, source_metadata):
        redistribution = source_metadata["redistribution"]
        assert redistribution["status"] == "permitted_with_attribution"
        assert redistribution["basis"] and redistribution["acknowledgement"]

    def test_every_computational_rule_cites_a_clause_from_a_named_document(self, pack):
        for rule in pack.evaluable_rules:
            assert rule.source_clause_refs, rule.id
            for ref in rule.source_clause_refs:
                assert ref.startswith(("car:3m4:rev4:", "car:3m2:rev3:")), (rule.id, ref)

    def test_both_instruments_are_actually_used(self, pack):
        refs = {ref for rule in pack.rules for ref in rule.source_clause_refs}
        assert any(ref.startswith("car:3m4:rev4:") for ref in refs)
        assert any(ref.startswith("car:3m2:rev3:") for ref in refs)


# ------------------------------------------------------------------- approval is pending


class TestApprovalIsGenuinelyPending:
    def test_no_reviewer_or_approval_is_recorded(self):
        review = yaml.safe_load((PACK_DIR / "review.yaml").read_text(encoding="utf-8"))
        assert review["review_status"] == "pending"
        assert review["reviewer_name"] is None
        assert review["approval"] is None
        assert review["rule_signoff"] == []

    def test_open_review_questions_are_recorded_and_unanswered(self):
        review = yaml.safe_load((PACK_DIR / "review.yaml").read_text(encoding="utf-8"))
        questions = review["open_review_questions"]
        assert len(questions) >= 8
        assert all(question["status"] == "unanswered" for question in questions)
        assert all(question.get("clause") for question in questions)

    def test_the_pack_is_not_approved_and_not_verified_eligible(self, pack):
        assert pack.status is PolicyPackStatus.official_guidance_dated
        assert pack.verified_mode_eligible is False

    def test_it_may_not_be_called_current_law(self, pack):
        """Archived sources do not replace status, currentness evidence or SME approval."""
        assert pack.source_document_verified is True
        assert pack.may_be_called_current_law is False

    def test_verified_mode_refuses_it(self):
        with pytest.raises(PackNotVerifiedEligible) as raised:
            load_pack(
                pack_dir=PACKS_ROOT,
                pack_id=PACK_ID,
                version=PACK_VERSION,
                mode=PolicyMode.verified,
            )
        assert raised.value.code == "PACK_NOT_VERIFIED_ELIGIBLE"

    def test_no_rule_claims_approved(self, pack):
        assert not [rule.id for rule in pack.rules if rule.status == "approved"]

    def test_the_badge_says_review_is_pending(self, pack):
        assert "PENDING SME REVIEW" in pack.ui_label.upper()

    def test_promotion_preconditions_are_written_down(self):
        manifest = yaml.safe_load((PACK_DIR / "pack.yaml").read_text(encoding="utf-8"))
        blockers = " ".join(manifest["blocks_verified_mode_until"]).lower()
        assert "sme" in blockers
        assert "current revision" in blockers
        assert "source.pdf" not in blockers


# ------------------------------------------------- differences against the charter pack


class TestMaterialDifferencesFromTheCharterPack:
    """The primary source disagrees with the 2019 booklet in four places.

    Each is encoded to the primary text and flagged for review rather than reconciled silently.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def charter(cls):
        return load_pack(
            pack_dir=PACKS_ROOT,
            pack_id="in-moca-charter-2019",
            version="2019.02",
            mode=PolicyMode.charter,
        )

    def test_hotel_no_longer_requires_advance_notice(self, pack, charter):
        """Para 3.4.3 carries no communication condition; the booklet's rule did."""
        car_rule = pack.rule("delay.care.hotel.long_or_night_window")
        charter_rule = charter.rule("delay.care.hotel.night_window")
        assert "event.notice_minutes" in str(charter_rule.when)
        assert "event.notice_minutes" not in str(car_rule.when)

    def test_delay_exemption_now_reaches_the_facilities(self, pack, charter):
        """Para 3.4.4 excuses Para 3.8 itself, so meals and hotel are suppressed."""
        car_rule = pack.rule("delay.exemption.extraordinary_circumstances")
        suppressed = set(car_rule.effect["suppresses_entitlement_types"])
        assert {"meals_refreshments", "hotel_accommodation"} <= suppressed

        charter_rule = charter.rule("exemption.atc_weather_security_delay")
        preserved = set(charter_rule.effect["preserves_entitlement_types"])
        assert "meals_refreshments" in preserved, "the booklet preserved care; the CAR does not"

    def test_agent_refund_onus_moved_to_the_airline(self, pack, charter):
        """Part II Rev. 3 Para 3(c): airline's onus, 14 working days."""
        car_rule = pack.rule("refund.timing.travel_agent_14_working_days")
        assert car_rule.entitlement["onus_on"] == "airline"
        assert car_rule.entitlement["deadline_working_days"] == 14
        assert charter.rule("refund.timing.travel_agent").entitlement["payer"] == "travel_agent"

    def test_the_free_cancellation_window_is_resolved_at_48_hours(self, pack, charter):
        """The booklet's 24-hour rule was excluded as superseded. Part II Rev. 3 answers it."""
        excluded = charter.rule("booking.free_cancel_or_amend_within_24h")
        assert excluded.excluded_from_evaluation is True

        car_rule = pack.rule("refund.look_in_option_48h")
        assert car_rule.excluded_from_evaluation is False
        assert "2880" in str(car_rule.when), "48 hours, expressed in minutes"

    def test_this_pack_excludes_nothing(self, pack):
        """Nothing here rests on secondary reporting, so no rule is superseded-suspect."""
        assert pack.excluded_rules == []

    def test_both_packs_agree_on_every_cash_figure(self, pack, charter):
        """The booklet's figures were right; only their framing and provenance were weaker."""
        pairs = [
            ("cancellation.compensation.block_upto_60", "cancellation.compensation.block_upto_60"),
            (
                "cancellation.compensation.block_60_to_120",
                "cancellation.compensation.block_60_to_120",
            ),
            (
                "cancellation.compensation.block_over_120",
                "cancellation.compensation.block_over_120",
            ),
            ("denied_boarding.alternate_within_24h", "denied_boarding.alternate_within_24h"),
            ("denied_boarding.alternate_beyond_24h", "denied_boarding.alternate_beyond_24h"),
            ("denied_boarding.no_alternate_taken", "denied_boarding.no_alternate_taken"),
        ]
        for car_id, charter_id in pairs:
            car_entitlement = pack.rule(car_id).entitlement
            charter_entitlement = charter.rule(charter_id).entitlement
            assert car_entitlement.get("cap_inr") == charter_entitlement.get("cap_inr"), car_id
            assert car_entitlement.get("percentage") == charter_entitlement.get("percentage"), (
                car_id
            )

    def test_the_two_packs_are_distinct_identities(self, pack, charter):
        assert pack.pack_hash != charter.pack_hash
        assert pack.pack_id != charter.pack_id
