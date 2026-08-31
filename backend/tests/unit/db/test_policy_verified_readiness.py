"""What the record does once an approved pack exists — Phase 4 G1/G2 readiness, Stream C side.

**No approved India pack exists.** `policy_packs/` holds the dated MoCA charter and a fictional demo
fixture; there is no archived primary document anywhere in the repository, `review.yaml` records no
reviewer, and `POLICY_MODE=verified` is refused before the loader is consulted. G1 needs the current
DGCA CAR and an authorised sign-off, and neither may be invented — see the PR for the blocker list.

Everything *downstream* of that pack can still be proven, and this file proves it. Stream B covers
the loader ladder (`tests/unit/policy/test_loader.py`, `test_source_integrity.py`), and
`test_policy_ingest.py` covers ingesting the charter pack. What nothing covered is the persistence
and decision chain for an **approved** pack: that ingestion records `approved`, verified
eligibility, the reviewer and a real digest rather than the sentinel; that `resolver_hash` and
`entitlement_evaluation` behave under verified mode; and that removing any single precondition still
fails closed. If that is first exercised the day the real pack lands, it is exercised in a hurry.

## The pack these tests build

Written into `tmp_path` per test and never committed. It fabricates nothing: jurisdiction `ZZ` and
its authority do not exist, its one rule is non-cash so no figure is invented, its clause reference
is visibly synthetic, and its reviewer is named as a test fixture. It stands in for the *shape* of
an approved pack, never for its content.

Owner: Stream C.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import select

from app.config import PolicyMode
from app.db.policy_ingest import ingest_pack, record_entitlement_evaluation, record_resolution
from app.errors import PackNotVerifiedEligible, PolicyPackUnavailable
from app.models.enums import PolicyPackStatus
from app.models.policy import (
    EntitlementEvaluation,
    PolicyApplicability,
    PolicyClause,
    PolicyPack,
    PolicyRule,
    PolicySourceDocument,
)
from app.policy.entitlements import calculate
from app.policy.loader import PENDING_ARCHIVAL, load_pack
from app.policy.resolver import select as resolve_select
from tests.unit.db.conftest import PACKS_ROOT

SYNTHETIC_ID = "zz-synthetic-approved"
SYNTHETIC_VERSION = "1.0"
SYNTHETIC_CLAUSE = "synthetic:clause:care-1"
SYNTHETIC_RULE = "synthetic.care.refreshments"
SYNTHETIC_REVIEWER = "Test Fixture Reviewer (not a real person)"
SOURCE_BYTES = b"%PDF-1.7 synthetic test document, not a regulation\n"

FACTS = {
    "itinerary": {"origin_country": "ZZ", "destination_country": "ZZ"},
    "operating_carrier": {"id": "ZZ", "country": "ZZ"},
    "event": {"type": "delay", "delay_minutes": 120},
}


def _write(path: Path, document: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def write_synthetic_approved_pack(root: Path, **manifest_overrides: Any) -> Path:
    """A structurally complete approved pack, in the shape the loader demands.

    Synthetic throughout, deliberately: `ZZ` is not a jurisdiction, the authority does not exist,
    the single rule is non-cash so no monetary figure is invented, and the clause reference names
    itself as synthetic. Returns the packs *root*, so it can be passed to `load_pack` directly.
    """
    directory = root / SYNTHETIC_ID / SYNTHETIC_VERSION
    directory.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "id": SYNTHETIC_ID,
        "version": SYNTHETIC_VERSION,
        "jurisdiction": "ZZ",
        "authority": "Synthetic Test Authority (does not exist)",
        "document": "Synthetic test instrument — fixture only, not a regulation",
        "document_date": "2026-01-01",
        "currency": "INR",
        "status": "approved",
        "verified_mode_eligible": True,
        "ui_label": "SYNTHETIC TEST PACK · NOT LAW",
        "precedence": {"conflict_rules_defined": True, "on_conflict": "needs_human"},
        "required_context": ["itinerary", "operating_carrier", "event"],
    }
    manifest.update(manifest_overrides)
    _write(directory / "pack.yaml", manifest)

    _write(
        directory / "applicability.yaml",
        {
            "pack": SYNTHETIC_ID,
            "version": SYNTHETIC_VERSION,
            "required_facts": [
                "itinerary.origin_country",
                "itinerary.destination_country",
                "operating_carrier.id",
                "event.type",
            ],
            "applies_when": {"any_of": [{"itinerary.origin_country": "ZZ"}]},
            "on_missing_required_fact": "undetermined",
            "on_undetermined": "needs_human",
        },
    )

    _write(
        directory / "rules.yaml",
        {
            "pack": SYNTHETIC_ID,
            "version": SYNTHETIC_VERSION,
            "rules": [
                {
                    "id": SYNTHETIC_RULE,
                    "status": "approved",
                    "scope": "all",
                    "source_clause_refs": [SYNTHETIC_CLAUSE],
                    "interpretation": (
                        "SYNTHETIC. A delay of 90 minutes or more entitles a passenger to "
                        "refreshments. Invented for a test fixture and corresponding to no "
                        "instrument anywhere."
                    ),
                    "when": {
                        "all": [
                            {"fact": "event.type", "op": "eq", "value": "delay"},
                            {"fact": "event.delay_minutes", "op": "gte", "value": 90},
                        ]
                    },
                    "entitlement": {"type": "meals_refreshments", "cash": False},
                }
            ],
        },
    )

    _write(
        directory / "review.yaml",
        {
            "pack": SYNTHETIC_ID,
            "version": SYNTHETIC_VERSION,
            "review_status": "approved",
            "reviewer_name": SYNTHETIC_REVIEWER,
            "reviewer_role": "test fixture",
            "reviewer_organisation": "none",
            "reviewed_at": "2026-01-02",
            "approval": "approved for the purposes of this test only",
            "open_review_questions": [],
            "rule_signoff": [{"rule": SYNTHETIC_RULE, "decision": "approved"}],
        },
    )

    (directory / "source.pdf").write_bytes(SOURCE_BYTES)
    _write(
        directory / "source-metadata.yaml",
        {
            "title": "Synthetic test instrument",
            "publisher": "Synthetic Test Authority (does not exist)",
            "published": "2026-01",
            "official_url": "https://example.invalid/synthetic",
            "local_path": "source.pdf",
            "content_sha256": hashlib.sha256(SOURCE_BYTES).hexdigest(),
            "archived": True,
            "extraction_method": "test_fixture",
            "redistribution": {"status": "not_applicable"},
        },
    )
    return root


def _load(root: Path, *, mode: PolicyMode = PolicyMode.verified):
    return load_pack(pack_dir=root, pack_id=SYNTHETIC_ID, version=SYNTHETIC_VERSION, mode=mode)


@pytest.fixture
def verified_pack(tmp_path: Path):
    """A pack that satisfies every precondition, loaded in verified mode."""
    return _load(write_synthetic_approved_pack(tmp_path / "packs"))


class TestThePreconditionSetIsSatisfiable:
    """If this fails, G1 is blocked on the code as well as on the source material."""

    def test_a_complete_approved_pack_loads_in_verified_mode(self, verified_pack):
        assert verified_pack.status is PolicyPackStatus.approved
        assert verified_pack.verified_mode_eligible is True
        assert verified_pack.loaded_mode is PolicyMode.verified

    def test_it_is_the_only_kind_of_pack_that_may_be_called_current_law(self, verified_pack):
        """The property the charter pack can never have, and the reason verified mode exists."""
        assert verified_pack.may_be_called_current_law is True
        assert verified_pack.source_document_verified is True
        assert verified_pack.citations_permitted is True

    def test_the_charter_pack_still_cannot(self, charter):
        assert charter.may_be_called_current_law is False
        assert charter.source_content_sha256 == PENDING_ARCHIVAL


class TestIngestionRecordsAnApprovedPackFaithfully:
    """Everything here is asserted against the charter pack elsewhere; none of it was asserted
    for a pack whose standing actually matters."""

    async def test_the_standing_is_persisted_as_published(self, session, verified_pack):
        await ingest_pack(session, pack=verified_pack)

        row = (await session.execute(select(PolicyPack))).scalars().one()
        assert row.status == PolicyPackStatus.approved
        assert row.verified_mode_eligible is True
        assert row.pack_hash == verified_pack.pack_hash

    async def test_the_reviewer_and_approval_date_reach_the_record(self, session, verified_pack):
        """An approved pack is approved because a person signed it, so the record names them."""
        await ingest_pack(session, pack=verified_pack)

        row = (await session.execute(select(PolicyPack))).scalars().one()
        assert row.reviewed_by == SYNTHETIC_REVIEWER
        assert row.reviewed_at is not None
        assert row.reviewed_at.date().isoformat() == "2026-01-02"

    async def test_a_real_digest_is_stored_rather_than_the_sentinel(self, session, verified_pack):
        await ingest_pack(session, pack=verified_pack)

        document = (await session.execute(select(PolicySourceDocument))).scalars().one()
        assert document.content_hash == hashlib.sha256(SOURCE_BYTES).hexdigest()
        assert document.content_hash != PENDING_ARCHIVAL

    async def test_the_cited_clause_becomes_a_clause_row(self, session, verified_pack):
        await ingest_pack(session, pack=verified_pack)

        clause = (await session.execute(select(PolicyClause))).scalars().one()
        assert clause.clause_ref == SYNTHETIC_CLAUSE
        rule = (await session.execute(select(PolicyRule))).scalars().one()
        assert rule.source_clause_refs == [SYNTHETIC_CLAUSE]
        assert rule.review_status == "approved"


class TestTheDecisionChainWorksUnderVerifiedMode:
    async def test_resolver_hash_and_entitlement_are_recorded(self, session, verified_pack):
        """G5 and G6 end to end against an approved pack, not just a dated one."""
        await ingest_pack(session, pack=verified_pack)
        resolution = resolve_select(trip_context=FACTS, packs=[verified_pack])
        assert resolution.selected == [SYNTHETIC_ID]

        record = await record_resolution(
            session,
            incident_id=1,
            resolution=resolution,
            packs=[verified_pack],
            trip_context=FACTS,
        )
        cited = calculate(facts=FACTS, pack=verified_pack, resolve_applicability=False)
        assert SYNTHETIC_RULE in cited.rules_fired

        rows = await record_entitlement_evaluation(
            session,
            incident_id=1,
            applicability=record.applicability[0],
            cited=cited,
            trip_context=FACTS,
        )

        assert len(record.resolver_hash) == 32
        applicability = (await session.execute(select(PolicyApplicability))).scalars().one()
        assert applicability.resolver_hash == record.resolver_hash
        assert len(rows) == 1
        stored = (await session.execute(select(EntitlementEvaluation))).scalars().one()
        assert stored.result["pack_hash"] == verified_pack.pack_hash

    async def test_the_recorded_result_carries_the_standing_that_permits_current_law(
        self, session, verified_pack
    ):
        """The distinction a reader needs, recoverable from the row rather than re-derived."""
        await ingest_pack(session, pack=verified_pack)
        resolution = resolve_select(trip_context=FACTS, packs=[verified_pack])
        record = await record_resolution(
            session,
            incident_id=1,
            resolution=resolution,
            packs=[verified_pack],
            trip_context=FACTS,
        )
        cited = calculate(facts=FACTS, pack=verified_pack, resolve_applicability=False)

        await record_entitlement_evaluation(
            session,
            incident_id=1,
            applicability=record.applicability[0],
            cited=cited,
            trip_context=FACTS,
        )

        stored = (await session.execute(select(EntitlementEvaluation))).scalars().one()
        assert stored.result["pack_status"] == PolicyPackStatus.approved.value
        assert cited.may_be_presented_as_current_law is True


class TestRemovingAnySinglePreconditionFailsClosed:
    """One at a time, from an otherwise complete pack, so each guard is shown to be load-bearing."""

    def test_without_verified_eligibility(self, tmp_path):
        root = write_synthetic_approved_pack(tmp_path / "packs", verified_mode_eligible=False)

        with pytest.raises(PackNotVerifiedEligible, match=r"verified_mode_eligible"):
            _load(root)

    def test_without_approved_status(self, tmp_path):
        root = write_synthetic_approved_pack(tmp_path / "packs", status="official_guidance_dated")

        with pytest.raises(PackNotVerifiedEligible):
            _load(root)

    def test_without_a_recorded_reviewer(self, tmp_path):
        root = write_synthetic_approved_pack(tmp_path / "packs")
        review = root / SYNTHETIC_ID / SYNTHETIC_VERSION / "review.yaml"
        _write(review, {**yaml.safe_load(review.read_text()), "reviewer_name": None})

        with pytest.raises(PolicyPackUnavailable, match="without a recorded reviewer"):
            _load(root)

    def test_without_an_approval(self, tmp_path):
        root = write_synthetic_approved_pack(tmp_path / "packs")
        review = root / SYNTHETIC_ID / SYNTHETIC_VERSION / "review.yaml"
        _write(review, {**yaml.safe_load(review.read_text()), "approval": None})

        with pytest.raises(PolicyPackUnavailable, match="without a recorded reviewer"):
            _load(root)

    def test_without_a_clause_reference_on_every_computational_rule(self, tmp_path):
        root = write_synthetic_approved_pack(tmp_path / "packs")
        rules_path = root / SYNTHETIC_ID / SYNTHETIC_VERSION / "rules.yaml"
        document = yaml.safe_load(rules_path.read_text())
        document["rules"][0]["source_clause_refs"] = []
        _write(rules_path, document)

        with pytest.raises(PolicyPackUnavailable, match="lack source_clause_refs"):
            _load(root)

    def test_without_an_archived_source_document(self, tmp_path):
        root = write_synthetic_approved_pack(tmp_path / "packs")
        metadata = root / SYNTHETIC_ID / SYNTHETIC_VERSION / "source-metadata.yaml"
        _write(
            metadata,
            {
                **yaml.safe_load(metadata.read_text()),
                "archived": False,
                "content_sha256": PENDING_ARCHIVAL,
            },
        )

        with pytest.raises(PackNotVerifiedEligible):
            _load(root)

    def test_with_a_source_document_that_does_not_match_its_digest(self, tmp_path):
        root = write_synthetic_approved_pack(tmp_path / "packs")
        (root / SYNTHETIC_ID / SYNTHETIC_VERSION / "source.pdf").write_bytes(b"a different file")

        with pytest.raises(PackNotVerifiedEligible):
            _load(root)

    async def test_a_pack_refused_at_load_never_reaches_the_record(self, session, tmp_path):
        """The persistence-side statement of the same guarantee: no load, nothing to ingest."""
        root = write_synthetic_approved_pack(tmp_path / "packs", verified_mode_eligible=False)

        with pytest.raises(PackNotVerifiedEligible):
            await ingest_pack(session, pack=_load(root))

        assert (await session.execute(select(PolicyPack))).scalars().first() is None


class TestTheCommittedPacksAgreeWithTheLadder:
    """An invariant, not a snapshot: it holds before and after a real approved pack lands.

    A pack row is the thing a citation is read from, so a pack claiming verified eligibility that
    verified mode would refuse is the one inconsistency that must never be persistable.
    """

    @pytest.mark.parametrize(
        "pack_id,version",
        sorted(
            (path.parent.name, path.name)
            for path in PACKS_ROOT.glob("*/*")
            if (path / "pack.yaml").is_file()
        ),
    )
    def test_an_eligibility_claim_matches_what_verified_mode_grants(
        self, pack_id: str, version: str
    ):
        manifest = yaml.safe_load(
            (PACKS_ROOT / pack_id / version / "pack.yaml").read_text(encoding="utf-8")
        )
        claims_eligible = bool(manifest.get("verified_mode_eligible", False))

        try:
            load_pack(
                pack_dir=PACKS_ROOT,
                pack_id=pack_id,
                version=version,
                mode=PolicyMode.verified,
            )
        except (PackNotVerifiedEligible, PolicyPackUnavailable):
            granted = False
        else:
            granted = True

        assert granted == claims_eligible, (
            f"{pack_id}@{version} claims verified_mode_eligible={claims_eligible} but verified "
            f"mode {'accepts' if granted else 'refuses'} it"
        )
