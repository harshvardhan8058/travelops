"""G1 limited-approval readiness — review artefacts must stay internally true.

The two source PDFs are byte-verified and every rule remains mapped to its archived clause. The
project owner has approved the pack only as a project artifact with explicit operational gates;
that approval is not DGCA endorsement, external SME sign-off, proof of currentness, or permission
to load the pack in verified mode.

This suite enforces three boundaries:

  * `clause-verification.yaml` describes every rule exactly once and makes no machine-verification
    claim for scanned Part II;
  * `review-readiness.md`, `review.yaml`, the runtime gates, and per-rule sign-off agree;
  * approved project status cannot imply current-law standing while verified eligibility is false.

Deliberately free of any PDF library: the extraction findings are checked as a durable record.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.config import PolicyMode
from app.errors import PackNotVerifiedEligible
from app.models.enums import PolicyPackStatus
from app.policy.loader import load_pack

PACKS_ROOT = Path(__file__).resolve().parents[4] / "policy_packs"
PACK_ID = "in-dgca-car-3m4"
PACK_VERSION = "rev4-2023.01"
PACK_DIR = PACKS_ROOT / PACK_ID / PACK_VERSION

#: The two claims `clause-verification.yaml` is allowed to make about a rule.
TEXT_VERIFIED = "text_layer_verified"
VISUAL_REQUIRED = "visual_review_required"


def _yaml(name: str) -> dict[str, Any]:
    return yaml.safe_load((PACK_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rules() -> list[dict[str, Any]]:
    return _yaml("rules.yaml")["rules"]


@pytest.fixture(scope="module")
def verification() -> dict[str, Any]:
    return _yaml("clause-verification.yaml")


@pytest.fixture(scope="module")
def review() -> dict[str, Any]:
    return _yaml("review.yaml")


@pytest.fixture(scope="module")
def readiness() -> str:
    return (PACK_DIR / "review-readiness.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def pack():
    return load_pack(
        pack_dir=PACKS_ROOT, pack_id=PACK_ID, version=PACK_VERSION, mode=PolicyMode.charter
    )


# --------------------------------------------------------- the verification record is complete


class TestClauseVerificationCoversEveryRule:
    def test_it_describes_every_rule_exactly_once(self, rules, verification):
        recorded = [entry["id"] for entry in verification["rules"]]
        assert len(recorded) == len(set(recorded)), "a rule is recorded twice"
        assert set(recorded) == {rule["id"] for rule in rules}
        assert verification["summary"]["rules_total"] == len(rules)

    def test_the_recorded_clause_refs_match_the_rules(self, rules, verification):
        """The record cannot quietly describe a different encoding than the one that ships."""
        by_id = {entry["id"]: entry for entry in verification["rules"]}
        for rule in rules:
            assert by_id[rule["id"]]["source_clause_refs"] == rule["source_clause_refs"]

    def test_every_rule_carries_one_of_the_two_permitted_claims(self, verification):
        for entry in verification["rules"]:
            assert entry["verification"] in {TEXT_VERIFIED, VISUAL_REQUIRED}, entry["id"]

    def test_the_summary_counts_are_arithmetic_not_assertion(self, verification):
        entries = verification["rules"]
        summary = verification["summary"]
        text = sum(1 for e in entries if e["verification"] == TEXT_VERIFIED)
        visual = sum(1 for e in entries if e["verification"] == VISUAL_REQUIRED)
        assert summary[TEXT_VERIFIED] == text == 30
        assert summary[VISUAL_REQUIRED] == visual == 14
        assert text + visual == summary["rules_total"] == len(entries) == 44

    def test_no_clause_ref_or_figure_is_left_unresolved(self, verification):
        assert verification["summary"]["clause_refs_unresolved"] == 0
        assert verification["summary"]["figures_not_located_in_archived_text"] == 0


class TestNoRuleClaimsMoreThanItsDocumentSupports:
    """The whole point of the record. Part II is a scan; it cannot be string-matched."""

    def test_part_ii_is_recorded_as_not_machine_readable(self, verification):
        part_ii = verification["sources_as_verified"]["part_ii"]
        assert part_ii["machine_readable"] is False
        assert part_ii["extractable_text_chars"] < 100, (
            "if Part II gained a text layer, its rules may be machine-verified and this "
            "record must be regenerated rather than edited"
        )

    def test_part_iv_is_recorded_as_machine_readable(self, verification):
        part_iv = verification["sources_as_verified"]["part_iv"]
        assert part_iv["machine_readable"] is True
        assert part_iv["extractable_text_chars"] > 10_000

    def test_a_rule_reading_part_ii_is_never_recorded_as_text_verified(self, verification):
        for entry in verification["rules"]:
            cites_part_ii = any(ref.split(":")[1] == "3m2" for ref in entry["source_clause_refs"])
            if cites_part_ii:
                assert entry["verification"] == VISUAL_REQUIRED, entry["id"]

    def test_a_rule_reading_only_part_iv_is_text_verified(self, verification):
        for entry in verification["rules"]:
            docs = {ref.split(":")[1] for ref in entry["source_clause_refs"]}
            if docs == {"3m4"}:
                assert entry["verification"] == TEXT_VERIFIED, entry["id"]

    def test_the_recorded_source_hashes_match_the_archived_files(self, verification):
        """The record is pinned to the bytes it was produced from."""
        for key, filename in (("part_iv", "source.pdf"), ("part_ii", "source-part-ii.pdf")):
            recorded = verification["sources_as_verified"][key]
            assert recorded["local_path"] == filename
            archived = PACK_DIR / filename
            assert hashlib.sha256(archived.read_bytes()).hexdigest() == recorded["content_sha256"]

    def test_the_recorded_hashes_agree_with_source_metadata(self, verification):
        """Two files record these digests; they must not be allowed to disagree."""
        source = _yaml("source-metadata.yaml")
        [part_ii] = source["referenced_instruments"]
        assert (
            verification["sources_as_verified"]["part_iv"]["content_sha256"]
            == source["content_sha256"]
        )
        assert (
            verification["sources_as_verified"]["part_ii"]["content_sha256"]
            == part_ii["content_sha256"]
        )

    def test_the_verification_record_is_outside_the_pack_hash(self, pack):
        """Recording a verification run must not change what the pack MEANS."""
        from app.policy.loader import compute_pack_hash

        assert (PACK_DIR / "clause-verification.yaml").is_file()
        assert (PACK_DIR / "review-readiness.md").is_file()
        assert compute_pack_hash(PACK_DIR) == pack.pack_hash

    def test_the_readiness_note_quotes_the_live_pack_hash(self, pack, readiness):
        """A stale hash in a reviewer-facing document is a false statement about identity.

        `rules.yaml` and `review.yaml` are inside the pack hash, so any edit to them moves it.
        This caught exactly that: repointing one review reference changed the pack identity.
        """
        quoted = set(re.findall(r"\b[0-9a-f]{16}\b", readiness))
        assert quoted, "review-readiness.md records no pack hash at all"
        assert quoted == {pack.pack_hash}, (
            f"review-readiness.md quotes {sorted(quoted)}, live pack_hash is {pack.pack_hash}"
        )


# ------------------------------------------------------- the summary agrees with the record


class TestReviewReadinessMatchesReviewYaml:
    def test_every_open_question_appears_in_the_readiness_table(self, review, readiness):
        recorded = {question["id"] for question in review["open_review_questions"]}
        cited = set(re.findall(r"\bRQ-\d+\b", readiness))
        missing = recorded - cited
        assert not missing, f"open questions absent from review-readiness.md: {sorted(missing)}"

    def test_the_readiness_table_invents_no_question(self, review, readiness):
        recorded = {question["id"] for question in review["open_review_questions"]}
        cited = set(re.findall(r"\bRQ-\d+\b", readiness))
        # RQ-10 is named in both files only to explain that it was retired, never as open work.
        invented = cited - recorded - {"RQ-10"}
        assert not invented, f"review-readiness.md cites unknown questions: {sorted(invented)}"

    def test_rq_10_is_retired_and_not_reused(self, review):
        ids = {question["id"] for question in review["open_review_questions"]}
        assert "RQ-10" not in ids, "RQ-10 was retired; reusing the number hides the history"
        assert "RQ-11" in ids

    def test_question_dispositions_match_limited_approval(self, review):
        dispositions = {
            question["id"]: question["status"] for question in review["open_review_questions"]
        }
        assert dispositions == {
            "RQ-1": "resolved_project_decision",
            "RQ-2": "resolved_project_decision",
            "RQ-3": "operational_scope_required",
            "RQ-4": "operational_scope_required",
            "RQ-5": "operational_scope_required",
            "RQ-6": "resolved_project_decision",
            "RQ-7": "resolved_project_decision",
            "RQ-8": "resolved_project_decision",
            "RQ-9": "accepted_external_risk",
            "RQ-11": "resolved_project_decision",
        }

    def test_every_question_names_a_clause(self, review):
        for question in review["open_review_questions"]:
            assert question.get("clause"), question["id"]

    def test_questions_naming_rules_name_ones_that_exist(self, review, rules):
        known = {rule["id"] for rule in rules}
        for question in review["open_review_questions"]:
            named = []
            if question.get("affected_rule"):
                named.append(question["affected_rule"])
            named.extend(question.get("affected_rules") or [])
            for rule_id in named:
                assert rule_id in known, f"{question['id']} names unknown rule {rule_id}"

    def test_verified_blockers_name_only_the_unresolved_rqs(self, review):
        blockers = " ".join(_yaml("pack.yaml")["blocks_verified_mode_until"])
        assert {"RQ-3", "RQ-4", "RQ-5", "RQ-9"} == set(re.findall(r"\bRQ-\d+\b", blockers))
        assert len(review["open_review_questions"]) == 10


class TestNoDanglingReviewReferences:
    """A comment pointing at the wrong question is worse than no comment."""

    @pytest.mark.parametrize("filename", ["rules.yaml", "source-metadata.yaml", "test_cases.yaml"])
    def test_every_rq_referenced_in_the_pack_exists(self, filename, review):
        known = {question["id"] for question in review["open_review_questions"]}
        text = (PACK_DIR / filename).read_text(encoding="utf-8")
        referenced = set(re.findall(r"\bRQ-\d+\b", text))
        dangling = referenced - known
        assert not dangling, f"{filename} references non-existent {sorted(dangling)}"

    def test_the_look_in_rule_points_at_the_look_in_question(self, rules):
        """Regression: this note cited RQ-9, which is the unrelated currentness question."""
        [rule] = [r for r in rules if r["id"] == "refund.look_in_option_48h"]
        note = rule["entitlement"]["note"]
        assert "RQ-11" in note
        assert "RQ-9" not in note


# ------------------------------------------ project approval is not verified standing


class TestLimitedApprovalIsNotVerifiedStanding:
    def test_source_integrity_passes_now(self, pack):
        assert pack.source_document_verified is True
        assert pack.source_integrity_reason is None

    def test_project_approval_does_not_make_the_pack_verified_eligible(self, pack):
        assert pack.status is PolicyPackStatus.approved
        assert pack.verified_mode_eligible is False
        assert pack.may_be_called_current_law is False

    def test_verified_mode_still_refuses_it(self):
        with pytest.raises(PackNotVerifiedEligible) as raised:
            load_pack(
                pack_dir=PACKS_ROOT,
                pack_id=PACK_ID,
                version=PACK_VERSION,
                mode=PolicyMode.verified,
            )
        assert raised.value.code == "PACK_NOT_VERIFIED_ELIGIBLE"

    def test_project_approval_identity_and_scope_are_explicit(self, review):
        assert review["review_status"] == "approved_for_project_use_with_limitations"
        assert review["reviewer_name"] == "Project owner (project-provided approver)"
        assert review["approval"]["scope"] == "project_policy_artifact"
        assert review["approval"]["verified_mode_eligible"] is False
        assert str(review["approval"]["approved_at"]) == "2026-08-21"

    def test_all_rules_are_signed_off_only_for_limited_project_use(self, review, rules):
        signoff = review["rule_signoff"]
        ids = signoff["applies_to_rule_ids"]
        assert len(ids) == len(set(ids)) == 44
        assert set(ids) == {rule["id"] for rule in rules}
        assert signoff["decision"] == "approved_for_project_use_with_limitations"
        assert signoff["regulatory_approval"] is False

    def test_operational_questions_are_fail_closed(self, review):
        by_id = {question["id"]: question for question in review["open_review_questions"]}
        assert by_id["RQ-3"]["required_fact"] == (
            "cancellation.compensation_branch_confirmed_by_project_reviewer"
        )
        assert by_id["RQ-4"]["required_fact"] == (
            "fare.component_definition_confirmed_by_project_reviewer"
        )
        assert by_id["RQ-5"]["required_facts"] == [
            "cause_evidence.external_to_carrier",
            "cause_evidence.unavoidable_despite_reasonable_measures",
            "cause_evidence.evidence_refs",
        ]
        assert all(
            by_id[rq]["operational_disposition"] == "needs_human" for rq in ("RQ-3", "RQ-4", "RQ-5")
        )

    def test_rq_9_is_accepted_risk_without_a_currentness_claim(self, review):
        rq_9 = next(q for q in review["open_review_questions"] if q["id"] == "RQ-9")
        assert rq_9["status"] == "accepted_external_risk"
        assert rq_9["acceptance_scope"] == "project_policy_artifact_charter_mode_only"
        assert rq_9["currentness_asserted"] is False
        assert rq_9["blocks_verified_mode"] is True

    def test_readiness_note_draws_the_non_regulatory_boundary(self, readiness):
        lowered = readiness.lower()
        for required in (
            "approved only as a project policy artifact with limitations",
            "not dgca-approved",
            "not regulator-endorsed",
            "not verified as current law",
        ):
            assert required in lowered
        for forbidden in (
            "approved by dgca",
            "dgca endorsement granted",
            "regulatory approval granted",
            "external sme approval granted",
        ):
            assert forbidden not in lowered
