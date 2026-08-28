"""G3 — source-document integrity.

The archived primary document and its hash are the legal source; extracted text is not. Until now
`content_sha256` and `archived` were recorded and never read, so a pack could claim `approved` with
`PENDING_ARCHIVAL` behind it and nothing would notice.

Where the check applies is the whole design: a **dated** pack in charter mode records the truth
about its unarchived source and continues — that is precisely why the charter is not verified —
while verified mode and any pack claiming `approved` are refused. Asserted by
`TestWhereIntegrityIsEnforced`.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest
import yaml

from app.config import PolicyMode
from app.errors import PackNotVerifiedEligible, PolicyPackUnavailable
from app.policy.loader import (
    PENDING_ARCHIVAL,
    REASON_SOURCE_DOCUMENT_UNVERIFIED,
    compute_pack_hash,
    load_pack,
    verify_source_document,
)

PACKS_ROOT = Path(__file__).resolve().parents[4] / "policy_packs"
CHARTER = ("in-moca-charter-2019", "2019.02")

DOCUMENT = b"%PDF-1.4 fictional archived primary source for testing\n"
DOCUMENT_SHA = hashlib.sha256(DOCUMENT).hexdigest()


@pytest.fixture
def pack_copy(tmp_path: Path) -> Path:
    """A writable copy of the charter pack, so a test can archive or corrupt its source."""
    destination = tmp_path / "policy_packs" / CHARTER[0] / CHARTER[1]
    shutil.copytree(PACKS_ROOT / CHARTER[0] / CHARTER[1], destination)
    return tmp_path / "policy_packs"


def _pack_dir(root: Path) -> Path:
    return root / CHARTER[0] / CHARTER[1]


def _rewrite(root: Path, filename: str, mutate) -> None:
    path = _pack_dir(root) / filename
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    mutate(document)
    path.write_text(yaml.safe_dump(document), encoding="utf-8")


def _archive(root: Path, *, content: bytes = DOCUMENT, sha: str | None = None) -> None:
    """Write a source document and record its hash, as archiving the primary source would."""
    (_pack_dir(root) / "source.pdf").write_bytes(content)
    _rewrite(
        root,
        "source-metadata.yaml",
        lambda doc: doc.update(
            {
                "archived": True,
                "local_path": "source.pdf",
                "content_sha256": sha or hashlib.sha256(content).hexdigest(),
            }
        ),
    )


def _approve(root: Path) -> None:
    """Everything except source integrity that an approved pack needs."""
    _rewrite(
        root,
        "pack.yaml",
        lambda doc: doc.update({"status": "approved", "verified_mode_eligible": True}),
    )
    _rewrite(
        root,
        "review.yaml",
        lambda doc: doc.update({"approval": "granted", "reviewer_name": "Test SME"}),
    )


def _load(root: Path, mode: PolicyMode = PolicyMode.charter):
    return load_pack(pack_dir=root, pack_id=CHARTER[0], version=CHARTER[1], mode=mode)


# ------------------------------------------------------------------------ the hash itself


class TestVerifySourceDocument:
    def test_a_matching_hash_verifies(self, pack_copy: Path):
        _archive(pack_copy)
        source = yaml.safe_load(
            (_pack_dir(pack_copy) / "source-metadata.yaml").read_text(encoding="utf-8")
        )
        verified, reason = verify_source_document(directory=_pack_dir(pack_copy), source=source)
        assert verified is True
        assert reason is None

    def test_a_mismatched_hash_is_rejected(self, pack_copy: Path):
        """The file is not the document that was reviewed."""
        _archive(pack_copy, sha=hashlib.sha256(b"a different document").hexdigest())
        source = yaml.safe_load(
            (_pack_dir(pack_copy) / "source-metadata.yaml").read_text(encoding="utf-8")
        )
        verified, reason = verify_source_document(directory=_pack_dir(pack_copy), source=source)
        assert verified is False
        assert reason is not None and "not the document that was reviewed" in reason

    def test_an_uppercase_recorded_hash_still_matches(self, pack_copy: Path):
        """Hex case is not a semantic difference; a spurious mismatch would be a false alarm."""
        _archive(pack_copy, sha=DOCUMENT_SHA.upper())
        source = yaml.safe_load(
            (_pack_dir(pack_copy) / "source-metadata.yaml").read_text(encoding="utf-8")
        )
        assert verify_source_document(directory=_pack_dir(pack_copy), source=source)[0] is True

    @pytest.mark.parametrize(
        ("field", "value", "fragment"),
        [
            ("archived", False, "not archived"),
            ("content_sha256", PENDING_ARCHIVAL, PENDING_ARCHIVAL),
            ("content_sha256", None, "absent"),
            ("content_sha256", "", "absent"),
            ("content_sha256", "not-a-hash", "not SHA-256 hex"),
            ("content_sha256", "abc123", "not SHA-256 hex"),
            ("local_path", None, "`local_path` is absent"),
        ],
    )
    def test_missing_or_malformed_metadata_is_never_valid(
        self, pack_copy: Path, field: str, value, fragment: str
    ):
        """A missing value is not a verified one."""
        _archive(pack_copy)
        _rewrite(pack_copy, "source-metadata.yaml", lambda doc: doc.update({field: value}))
        source = yaml.safe_load(
            (_pack_dir(pack_copy) / "source-metadata.yaml").read_text(encoding="utf-8")
        )
        verified, reason = verify_source_document(directory=_pack_dir(pack_copy), source=source)
        assert verified is False
        assert reason is not None and fragment in reason

    def test_a_recorded_hash_with_no_file_on_disk_is_rejected(self, pack_copy: Path):
        _archive(pack_copy)
        (_pack_dir(pack_copy) / "source.pdf").unlink()
        source = yaml.safe_load(
            (_pack_dir(pack_copy) / "source-metadata.yaml").read_text(encoding="utf-8")
        )
        verified, reason = verify_source_document(directory=_pack_dir(pack_copy), source=source)
        assert verified is False
        assert reason is not None and "is not present" in reason

    def test_a_single_byte_change_is_detected(self, pack_copy: Path):
        _archive(pack_copy)
        (_pack_dir(pack_copy) / "source.pdf").write_bytes(DOCUMENT + b" ")
        source = yaml.safe_load(
            (_pack_dir(pack_copy) / "source-metadata.yaml").read_text(encoding="utf-8")
        )
        assert verify_source_document(directory=_pack_dir(pack_copy), source=source)[0] is False


# --------------------------------------------------------------- where it is enforced


class TestWhereIntegrityIsEnforced:
    def test_verified_mode_refuses_an_unverified_source(self, pack_copy: Path):
        _approve(pack_copy)  # approved and eligible, but nothing archived
        with pytest.raises(PackNotVerifiedEligible) as raised:
            _load(pack_copy, PolicyMode.verified)
        assert raised.value.details["reason_code"] == REASON_SOURCE_DOCUMENT_UNVERIFIED

    def test_verified_mode_refuses_a_hash_mismatch(self, pack_copy: Path):
        _approve(pack_copy)
        _archive(pack_copy, sha=hashlib.sha256(b"something else").hexdigest())
        with pytest.raises(PackNotVerifiedEligible) as raised:
            _load(pack_copy, PolicyMode.verified)
        assert raised.value.details["reason_code"] == REASON_SOURCE_DOCUMENT_UNVERIFIED

    def test_verified_mode_refuses_pending_archival(self, pack_copy: Path):
        _approve(pack_copy)
        _rewrite(
            pack_copy,
            "source-metadata.yaml",
            lambda doc: doc.update({"archived": True, "content_sha256": PENDING_ARCHIVAL}),
        )
        with pytest.raises(PackNotVerifiedEligible) as raised:
            _load(pack_copy, PolicyMode.verified)
        assert raised.value.details["reason_code"] == REASON_SOURCE_DOCUMENT_UNVERIFIED

    def test_an_approved_pack_is_refused_in_any_mode_without_a_verified_source(
        self, pack_copy: Path
    ):
        """`approved` recorded in review.yaml is a claim about a document someone must hold."""
        _approve(pack_copy)
        with pytest.raises(PackNotVerifiedEligible) as raised:
            _load(pack_copy, PolicyMode.charter)
        assert raised.value.details["reason_code"] == REASON_SOURCE_DOCUMENT_UNVERIFIED

    def test_a_dated_pack_records_the_truth_and_still_loads_in_charter_mode(self):
        """The charter's PDF is unarchived, which is exactly why it is not verified.

        Refusing it here would break Phase 1-3, and would be the wrong lesson: a dated pack is
        allowed to compute labelled figures, and its source status is reported rather than fatal.
        """
        pack = _load(PACKS_ROOT, PolicyMode.charter)
        assert pack.source_document_verified is False
        assert pack.source_archived is False
        assert pack.source_content_sha256 == PENDING_ARCHIVAL
        assert pack.source_integrity_reason is not None
        assert pack.may_be_called_current_law is False

    def test_archiving_the_source_makes_the_pack_verifiable(self, pack_copy: Path):
        """The positive path: archive, hash, approve, and verified mode accepts it."""
        _approve(pack_copy)
        _archive(pack_copy)
        pack = _load(pack_copy, PolicyMode.verified)
        assert pack.source_document_verified is True
        assert pack.source_archived is True
        assert pack.source_content_sha256 == DOCUMENT_SHA
        assert pack.may_be_called_current_law is True

    def test_existing_approved_preconditions_are_still_enforced(self, pack_copy: Path):
        """Source integrity is an addition, not a replacement.

        Archived and hashed, but no recorded reviewer: still refused, and for the original reason.
        """
        _archive(pack_copy)
        _rewrite(
            pack_copy,
            "pack.yaml",
            lambda doc: doc.update({"status": "approved", "verified_mode_eligible": True}),
        )
        with pytest.raises(PolicyPackUnavailable, match="reviewer"):
            _load(pack_copy, PolicyMode.verified)

    def test_an_uncited_rule_still_refuses_an_archived_approved_pack(self, pack_copy: Path):
        _approve(pack_copy)
        _archive(pack_copy)

        def strip(document):
            document["rules"][0]["source_clause_refs"] = []

        _rewrite(pack_copy, "rules.yaml", strip)
        with pytest.raises(PolicyPackUnavailable, match="source_clause_refs"):
            _load(pack_copy, PolicyMode.verified)


# ----------------------------------------------------------- pack identity stays separate


class TestSourceHashIsNotPackIdentity:
    def test_archiving_the_source_does_not_change_the_pack_hash(self, pack_copy: Path):
        """Recording a retrieval must not invalidate the hash a past evaluation was pinned to.

        `source-metadata.yaml` stays outside `_HASHED_FILES` deliberately: source integrity and
        pack identity are separate facts and stay separate.
        """
        before = compute_pack_hash(_pack_dir(pack_copy))
        _archive(pack_copy)
        assert compute_pack_hash(_pack_dir(pack_copy)) == before

    def test_the_charter_pack_hash_is_unchanged_by_this_work(self):
        """A Phase 1-3 evaluation pinned to this hash must still match."""
        assert compute_pack_hash(PACKS_ROOT / CHARTER[0] / CHARTER[1]) == "9c860cad8cfabab3"
