"""The status ladder, and the guards that keep an unreviewed pack unreviewed.

The single most important assertion here is that the charter pack cannot be loaded in verified
mode. Everything else exists to stop the pack quietly climbing the ladder by hand.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from app.config import PolicyMode
from app.errors import PackNotVerifiedEligible, PolicyPackUnavailable
from app.models.enums import PolicyPackStatus
from app.policy.loader import compute_pack_hash, load_pack

PACKS_ROOT = Path(__file__).resolve().parents[4] / "policy_packs"
PACK_ID = "in-moca-charter-2019"
PACK_VERSION = "2019.02"


def _load(mode: PolicyMode = PolicyMode.charter, root: Path | None = None):
    return load_pack(pack_dir=root or PACKS_ROOT, pack_id=PACK_ID, version=PACK_VERSION, mode=mode)


@pytest.fixture
def pack_copy(tmp_path: Path) -> Path:
    """A writable copy, so a test can corrupt a pack without touching the real one."""
    destination = tmp_path / "policy_packs" / PACK_ID / PACK_VERSION
    shutil.copytree(PACKS_ROOT / PACK_ID / PACK_VERSION, destination)
    return tmp_path / "policy_packs"


def _rewrite(root: Path, filename: str, mutate) -> None:
    path = root / PACK_ID / PACK_VERSION / filename
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    mutate(document)
    path.write_text(yaml.safe_dump(document), encoding="utf-8")


class TestStatusLadder:
    def test_verified_mode_rejects_the_charter_pack(self):
        """The case the whole ladder exists for."""
        with pytest.raises(PackNotVerifiedEligible) as raised:
            _load(PolicyMode.verified)
        assert raised.value.code == "PACK_NOT_VERIFIED_ELIGIBLE"

    def test_charter_mode_loads_it(self):
        pack = _load(PolicyMode.charter)
        assert pack.status is PolicyPackStatus.official_guidance_dated
        assert pack.verified_mode_eligible is False
        assert pack.ui_label.startswith("MoCA Passenger Charter")

    def test_demo_mode_refuses_a_real_authority_pack(self):
        """Demo mode proves the engine without citing anything.

        Loading a real government publication behind a fictional label would put genuine
        figures where the UI promises none.
        """
        with pytest.raises(PolicyPackUnavailable):
            _load(PolicyMode.demo)

    def test_verified_mode_still_rejects_an_approved_pack_that_is_not_eligible(
        self, pack_copy: Path
    ):
        _rewrite(pack_copy, "pack.yaml", lambda doc: doc.update({"status": "approved"}))
        _rewrite(
            pack_copy,
            "review.yaml",
            lambda doc: doc.update({"approval": "granted", "reviewer_name": "Test SME"}),
        )
        with pytest.raises(PackNotVerifiedEligible):
            _load(PolicyMode.verified, pack_copy)

    def test_a_draft_pack_computes_nothing_in_charter_mode(self, pack_copy: Path):
        _rewrite(pack_copy, "pack.yaml", lambda doc: doc.update({"status": "draft"}))
        with pytest.raises(PolicyPackUnavailable):
            _load(PolicyMode.charter, pack_copy)

    def test_a_retired_pack_computes_nothing(self, pack_copy: Path):
        _rewrite(pack_copy, "pack.yaml", lambda doc: doc.update({"status": "retired"}))
        with pytest.raises(PolicyPackUnavailable):
            _load(PolicyMode.charter, pack_copy)

    def test_an_unrecognised_status_is_rejected(self, pack_copy: Path):
        _rewrite(pack_copy, "pack.yaml", lambda doc: doc.update({"status": "probably_fine"}))
        with pytest.raises(PolicyPackUnavailable):
            _load(PolicyMode.charter, pack_copy)


class TestApprovalGuards:
    def test_an_approved_pack_needs_a_recorded_reviewer(self, pack_copy: Path):
        """This is why verified mode is unreachable today, and it should stay that way."""
        _rewrite(
            pack_copy,
            "pack.yaml",
            lambda doc: doc.update({"status": "approved", "verified_mode_eligible": True}),
        )
        with pytest.raises(PolicyPackUnavailable, match="reviewer"):
            _load(PolicyMode.verified, pack_copy)

    def test_an_approved_pack_needs_every_rule_cited(self, pack_copy: Path):
        def mutate(document):
            document["rules"][0]["source_clause_refs"] = []

        _rewrite(
            pack_copy,
            "pack.yaml",
            lambda doc: doc.update({"status": "approved", "verified_mode_eligible": True}),
        )
        _rewrite(
            pack_copy,
            "review.yaml",
            lambda doc: doc.update({"approval": "granted", "reviewer_name": "Test SME"}),
        )
        _rewrite(pack_copy, "rules.yaml", mutate)
        with pytest.raises(PolicyPackUnavailable, match="source_clause_refs"):
            _load(PolicyMode.verified, pack_copy)

    def test_a_rule_cannot_out_rank_its_pack(self, pack_copy: Path):
        """One edit must not be able to smuggle an approved figure into a dated pack."""

        def mutate(document):
            document["rules"][0]["status"] = "approved"

        _rewrite(pack_copy, "rules.yaml", mutate)
        with pytest.raises(PolicyPackUnavailable, match="out-rank"):
            _load(PolicyMode.charter, pack_copy)

    def test_no_rule_in_the_real_pack_is_marked_approved(self):
        pack = _load()
        assert not [rule.id for rule in pack.rules if rule.status == "approved"]

    def test_a_superseded_rule_must_also_be_excluded(self, pack_copy: Path):
        def mutate(document):
            for rule in document["rules"]:
                if rule["id"] == "booking.free_cancel_or_amend_within_24h":
                    rule["excluded_from_evaluation"] = False

        _rewrite(pack_copy, "rules.yaml", mutate)
        with pytest.raises(PolicyPackUnavailable, match="not excluded"):
            _load(PolicyMode.charter, pack_copy)


class TestPackIntegrity:
    def test_the_pack_has_forty_rules(self):
        assert len(_load().rules) == 40

    def test_the_twenty_four_hour_rule_is_excluded(self):
        pack = _load()
        assert [rule.id for rule in pack.excluded_rules] == [
            "booking.free_cancel_or_amend_within_24h"
        ]
        assert "booking.free_cancel_or_amend_within_24h" not in [
            rule.id for rule in pack.evaluable_rules
        ]

    def test_missing_directory_is_rejected(self, tmp_path: Path):
        with pytest.raises(PolicyPackUnavailable):
            load_pack(pack_dir=tmp_path, pack_id="nope", version="1", mode=PolicyMode.charter)

    def test_missing_pack_file_is_rejected(self, pack_copy: Path):
        (pack_copy / PACK_ID / PACK_VERSION / "pack.yaml").unlink()
        with pytest.raises(PolicyPackUnavailable, match=r"pack\.yaml"):
            _load(PolicyMode.charter, pack_copy)

    def test_a_pack_that_disagrees_with_its_own_path_is_rejected(self, pack_copy: Path):
        _rewrite(pack_copy, "pack.yaml", lambda doc: doc.update({"version": "2020.01"}))
        with pytest.raises(PolicyPackUnavailable, match="does not match"):
            _load(PolicyMode.charter, pack_copy)

    def test_duplicate_rule_ids_are_rejected(self, pack_copy: Path):
        def mutate(document):
            document["rules"].append(dict(document["rules"][0]))

        _rewrite(pack_copy, "rules.yaml", mutate)
        with pytest.raises(PolicyPackUnavailable, match="duplicate rule ids"):
            _load(PolicyMode.charter, pack_copy)

    def test_an_empty_rule_set_is_rejected(self, pack_copy: Path):
        _rewrite(pack_copy, "rules.yaml", lambda doc: doc.update({"rules": []}))
        with pytest.raises(PolicyPackUnavailable, match="no rules"):
            _load(PolicyMode.charter, pack_copy)

    def test_unreadable_yaml_is_rejected(self, pack_copy: Path):
        (pack_copy / PACK_ID / PACK_VERSION / "rules.yaml").write_text(
            "rules: [unclosed\n", encoding="utf-8"
        )
        with pytest.raises(PolicyPackUnavailable):
            _load(PolicyMode.charter, pack_copy)


class TestPackHash:
    def test_hash_is_stable(self):
        assert _load().pack_hash == _load().pack_hash

    def test_editing_a_rule_changes_the_pack_identity(self, pack_copy: Path):
        before = _load(PolicyMode.charter, pack_copy).pack_hash

        def mutate(document):
            document["rules"][0]["interpretation"] = "reworded"

        _rewrite(pack_copy, "rules.yaml", mutate)
        after = _load(PolicyMode.charter, pack_copy).pack_hash
        assert before != after, "an entitlement must be pinnable to the exact rule text"

    def test_adding_a_reviewer_test_case_does_not_change_the_identity(self, pack_copy: Path):
        """A past entitlement stays pinned to the rules it was computed from.

        If test_cases.yaml were hashed, adding a review case would make a replay look like it
        referenced a different pack when the rules had not moved at all.
        """
        before = compute_pack_hash(pack_copy / PACK_ID / PACK_VERSION)

        def mutate(document):
            document["cases"].append({"id": "reviewer_added_case", "expect": {}})

        _rewrite(pack_copy, "test_cases.yaml", mutate)
        assert compute_pack_hash(pack_copy / PACK_ID / PACK_VERSION) == before

    def test_changing_the_review_state_does_change_the_identity(self, pack_copy: Path):
        """Approval state governs what the pack may claim, so it is part of its identity."""
        before = compute_pack_hash(pack_copy / PACK_ID / PACK_VERSION)
        _rewrite(pack_copy, "review.yaml", lambda doc: doc.update({"reviewer_name": "Test SME"}))
        assert compute_pack_hash(pack_copy / PACK_ID / PACK_VERSION) != before

    def test_a_missing_semantic_file_is_a_different_pack(self, pack_copy: Path):
        before = compute_pack_hash(pack_copy / PACK_ID / PACK_VERSION)
        (pack_copy / PACK_ID / PACK_VERSION / "applicability.yaml").unlink()
        assert compute_pack_hash(pack_copy / PACK_ID / PACK_VERSION) != before


class TestClaimsAboutTheSource:
    def test_the_pack_may_not_be_called_current_law(self):
        assert _load().may_be_called_current_law is False

    def test_the_source_pdf_is_not_yet_archived(self):
        """Verified mode cannot be reached while the primary document has no hash."""
        pack = _load()
        assert pack.source["content_sha256"] == "PENDING_ARCHIVAL"
        assert pack.source["archived"] is False

    def test_review_is_still_pending_with_open_questions(self):
        pack = _load()
        assert pack.review["review_status"] == "pending"
        assert pack.review["approval"] is None
        unanswered = [
            question
            for question in pack.review["open_review_questions"]
            if question["status"] == "unanswered"
        ]
        assert len(unanswered) == 8

    def test_overlap_handling_is_undefined_so_conflicts_need_a_human(self):
        pack = _load()
        assert pack.conflict_rules_defined is False
        assert pack.on_conflict == "needs_human"
