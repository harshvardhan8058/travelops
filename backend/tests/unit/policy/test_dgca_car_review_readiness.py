"""G1 review readiness — the artefacts a reviewer is handed must stay true.

Archiving the two source PDFs closed the source-integrity precondition. It closed nothing else,
and the danger now is the opposite of the earlier one: not a missing hash, but a pack that looks
finished. These tests hold the review artefacts to the same standard as the rules.

Three things are enforced:

  * `clause-verification.yaml` describes every rule in `rules.yaml`, exactly once, and its claim
    about each rule is no stronger than the archived document supports. Part II is a scan with no
    text layer, so a rule reading Part II may never be recorded as machine-verified.
  * `review-readiness.md` and `review.yaml` agree on which questions are open. A reviewer must not
    be able to read a summary that has drifted from the authoritative record.
  * verification is not approval. Source integrity now passes, so these assert that the pack still
    refuses verified mode and still may not be called current law.

Deliberately free of any PDF library: the extraction was performed against the archived bytes and
its findings were written down, so this suite checks the record rather than re-running the tool.
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
        assert summary[TEXT_VERIFIED] == text
        assert summary[VISUAL_REQUIRED] == visual
        assert text + visual == summary["rules_total"] == len(entries)

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

    def test_every_question_is_still_unanswered(self, review):
        for question in review["open_review_questions"]:
            assert question["status"] == "unanswered", question["id"]

    def test_every_question_names_a_clause(self, review):
        for question in review["open_review_questions"]:
            assert question.get("clause"), question["id"]

    def test_questions_naming_a_rule_name_one_that_exists(self, review, rules):
        known = {rule["id"] for rule in rules}
        for question in review["open_review_questions"]:
            for key in ("affected_rule", "test_case"):
                value = question.get(key)
                if key == "affected_rule" and value:
                    assert value in known, f"{question['id']} names unknown rule {value}"

    def test_the_promotion_blockers_agree_on_the_question_count(self, review):
        """`pack.yaml` tells a reviewer how many questions to expect; it must not drift."""
        blockers = " ".join(_yaml("pack.yaml")["blocks_verified_mode_until"])
        count = len(review["open_review_questions"])
        assert "RQ-1 to RQ-9 and RQ-11" in blockers
        assert count == 10, f"the blocker text names ten questions but review.yaml has {count}"


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


# ------------------------------------------------- verification is not approval


class TestReadinessIsNotApproval:
    def test_source_integrity_passes_now(self, pack):
        assert pack.source_document_verified is True
        assert pack.source_integrity_reason is None

    def test_and_the_pack_is_still_not_verified_eligible(self, pack):
        assert pack.status is PolicyPackStatus.official_guidance_dated
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

    def test_the_remaining_preconditions_are_the_human_ones(self, review):
        """Exactly what the loader checks, and nothing this pack cannot honestly claim."""
        assert review["review_status"] == "pending"
        assert review["reviewer_name"] is None
        assert review["approval"] is None
        assert review["rule_signoff"] == []

    def test_the_readiness_note_does_not_claim_approval(self, readiness):
        lowered = readiness.lower()
        assert "not reviewed, not approved, and not current law" in lowered
        for forbidden in ("approved by", "signed off by", "sme approval granted"):
            assert forbidden not in lowered
