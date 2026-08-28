"""Policy pack → database ingestion — Phase 4 G6.

The policy tables have existed since the initial migration with nothing writing them, so a
recorded entitlement could only be explained by re-reading the pack directory it came from.
These tests pin what ingestion has to guarantee before the record can be trusted: it mirrors the
pack rather than accumulating copies of it, it preserves the document and clause provenance
verbatim, it validates a content hash it is asked to believe, and it never upgrades a pack's
standing just because the write succeeded.

Read through Stream B's loader throughout. Nothing here re-parses a pack.
"""

from __future__ import annotations

import hashlib
import shutil
from datetime import datetime
from pathlib import Path

import pytest
import yaml
from sqlalchemy import func, select

from app.config import PolicyMode
from app.db.policy_ingest import (
    CLAUSE_TEXT_UNAVAILABLE,
    EVENT_TYPE_ANY,
    EXTRACTION_REFERENCE_ONLY,
    PENDING_ARCHIVAL,
    ingest_pack,
    record_entitlement_evaluation,
    record_resolution,
)
from app.errors import PolicyPackUnavailable
from app.models.enums import ApplicabilityStatus, PolicyPackStatus
from app.models.policy import (
    EntitlementEvaluation,
    PolicyApplicability,
    PolicyClause,
    PolicyPack,
    PolicyRule,
    PolicySourceDocument,
)
from app.policy.loader import REASON_SOURCE_DOCUMENT_UNVERIFIED, load_pack
from app.policy.resolver import select as resolve_select
from tests.unit.db.conftest import CHARTER_ID, CHARTER_VERSION, PACKS_ROOT

TRIP_CONTEXT = {
    "itinerary": {
        "origin_country": "IN",
        "destination_country": "IN",
        "scheduled_departure_local": "2026-08-20T21:10:00+05:30",
    },
    "operating_carrier": {"id": "6E", "country": "IN"},
    "event": {"type": "delay", "travel_date": "2026-08-20", "delay_minutes": 420},
    "flight": {"block_time_minutes": 165},
}


async def _count(session, model) -> int:
    return int((await session.execute(select(func.count()).select_from(model))).scalar_one())


def _copy_pack(tmp_path: Path) -> Path:
    """A writable copy of the committed pack, so a test can vary it without touching Stream B's."""
    root = tmp_path / "policy_packs"
    destination = root / CHARTER_ID / CHARTER_VERSION
    shutil.copytree(PACKS_ROOT / CHARTER_ID / CHARTER_VERSION, destination)
    return root


def _rewrite(path: Path, **changes) -> None:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document.update(changes)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


class TestAValidPackIngests:
    async def test_the_report_summarises_what_it_did(self, session, charter):
        report = await ingest_pack(session, pack=charter)

        summary = report.summary()
        assert f"{CHARTER_ID}@{CHARTER_VERSION}" in summary
        assert charter.pack_hash in summary
        assert "verified_eligible=False" in summary

    async def test_the_pack_row_records_identity_status_and_hash(self, session, charter):
        report = await ingest_pack(session, pack=charter)

        row = (await session.execute(select(PolicyPack))).scalars().one()
        assert (row.pack_key, row.version) == (CHARTER_ID, CHARTER_VERSION)
        assert row.pack_hash == charter.pack_hash
        assert row.jurisdiction == charter.jurisdiction
        assert row.authority == charter.authority
        assert row.currency == charter.currency
        assert row.ui_label == charter.ui_label
        assert report.pack_created is True
        assert report.pack_hash == charter.pack_hash

    async def test_every_rule_in_the_pack_is_ingested(self, session, charter):
        report = await ingest_pack(session, pack=charter)

        assert report.rules_created == len(charter.rules)
        assert await _count(session, PolicyRule) == len(charter.rules)

    async def test_a_rule_keeps_its_clause_refs_interpretation_and_exclusion(
        self, session, charter
    ):
        await ingest_pack(session, pack=charter)
        source = charter.rule("delay.care.meals.block_150_to_300")

        row = (
            (await session.execute(select(PolicyRule).where(PolicyRule.rule_key == source.id)))
            .scalars()
            .one()
        )
        assert row.source_clause_refs == source.source_clause_refs
        assert row.interpretation == source.interpretation
        assert row.review_status == source.status
        assert row.excluded_from_evaluation is False

    async def test_a_superseded_rule_stays_excluded_after_ingestion(self, session, charter):
        """The exclusion is the whole reason the rule is safe to store at all."""
        await ingest_pack(session, pack=charter)
        excluded = {rule.id for rule in charter.excluded_rules}
        assert excluded, "the charter pack should carry at least one excluded rule"

        rows = (
            (
                await session.execute(
                    select(PolicyRule).where(PolicyRule.excluded_from_evaluation.is_(True))
                )
            )
            .scalars()
            .all()
        )

        assert {row.rule_key for row in rows} == excluded

    async def test_the_event_type_is_derived_from_the_rules_own_condition(self, session, charter):
        await ingest_pack(session, pack=charter)

        rows = (await session.execute(select(PolicyRule))).scalars().all()
        by_key = {row.rule_key: row.event_type for row in rows}

        assert by_key["delay.care.meals.block_150_to_300"] == "delay"
        assert all(value for value in by_key.values()), "event_type is NOT NULL"
        # A rule with no `event.type` leaf records that it is not scoped to one, rather than
        # being assigned an event it never mentioned.
        unscoped = {rule.id for rule in charter.rules if by_key.get(rule.id) == EVENT_TYPE_ANY}
        assert all("delay." not in rule_id for rule_id in unscoped)


class TestProvenanceSurvivesIngestion:
    async def test_the_source_document_keeps_its_title_url_and_local_path(self, session, charter):
        await ingest_pack(session, pack=charter)

        document = (await session.execute(select(PolicySourceDocument))).scalars().one()
        assert document.title == charter.source["title"]
        assert document.source_url == charter.source["official_url"]
        assert document.local_path == charter.source["local_path"]
        assert document.published_revision == str(charter.source["published"])

    async def test_the_retrieval_date_is_recorded(self, session, charter):
        await ingest_pack(session, pack=charter)

        document = (await session.execute(select(PolicySourceDocument))).scalars().one()
        assert document.retrieved_at is not None
        assert document.retrieved_at.date().isoformat() == str(charter.source["received_at"])

    def test_a_recorded_date_is_written_timezone_aware(self):
        """`retrieved_at` and `reviewed_at` are `timezone=True`.

        Asserted on the parser rather than on a read-back row, because SQLite has no aware type
        and returns a naive value whatever was written. What matters is that nothing naive is
        handed to Postgres, where the session timezone would decide the instant.
        """
        from datetime import UTC, date

        from app.db.policy_ingest import _iso_datetime

        assert _iso_datetime(date(2026, 8, 20)) == datetime(2026, 8, 20, tzinfo=UTC)
        assert _iso_datetime("2026-08-20").tzinfo is UTC
        assert _iso_datetime("2026-08-20T11:30:00+05:30").utcoffset().total_seconds() == 19800
        assert _iso_datetime(None) is None
        assert _iso_datetime("not a date") is None

    async def test_the_content_hash_is_preserved_verbatim(self, session, charter):
        """`PENDING_ARCHIVAL` is a fact about the record and must survive as written."""
        assert charter.source["content_sha256"] == PENDING_ARCHIVAL

        await ingest_pack(session, pack=charter)

        document = (await session.execute(select(PolicySourceDocument))).scalars().one()
        assert document.content_hash == PENDING_ARCHIVAL

    async def test_every_cited_clause_becomes_a_clause_row(self, session, charter):
        expected = {ref for rule in charter.rules for ref in rule.source_clause_refs if ref.strip()}

        await ingest_pack(session, pack=charter)

        rows = (await session.execute(select(PolicyClause))).scalars().all()
        assert {row.clause_ref for row in rows} == expected
        assert expected, "the charter pack should cite at least one clause"

    async def test_clause_text_is_marked_unavailable_rather_than_invented(self, session, charter):
        """The pack's own metadata says the text must be read from the archived original."""
        await ingest_pack(session, pack=charter)

        rows = (await session.execute(select(PolicyClause))).scalars().all()
        assert {row.text_content for row in rows} == {CLAUSE_TEXT_UNAVAILABLE}
        assert {row.extraction_method for row in rows} == {EXTRACTION_REFERENCE_ONLY}
        interpretations = {rule.interpretation for rule in charter.rules if rule.interpretation}
        assert not (interpretations & {row.text_content for row in rows}), (
            "our paraphrase must never be filed as the regulator's clause text"
        )

    async def test_the_document_date_the_pack_states_is_recorded(self, session, charter):
        from datetime import date

        assert charter.document_date == "2019-02-01"

        await ingest_pack(session, pack=charter)

        row = (await session.execute(select(PolicyPack))).scalars().one()
        assert row.effective_from == date(2019, 2, 1)

    async def test_a_month_only_document_date_does_not_become_a_precise_effective_date(
        self, session, charter
    ):
        """A month is not a date. Defaulting to the first would state an effective date the
        source never gave, on the row a citation is read from."""
        vague = charter.model_copy(update={"document_date": "2019-02"})

        await ingest_pack(session, pack=vague)

        row = (await session.execute(select(PolicyPack))).scalars().one()
        assert row.effective_from is None


class TestIngestionIsIdempotent:
    async def test_repeated_ingestion_creates_no_duplicate_records(self, session, charter):
        first = await ingest_pack(session, pack=charter)
        counts = (
            await _count(session, PolicyPack),
            await _count(session, PolicySourceDocument),
            await _count(session, PolicyClause),
            await _count(session, PolicyRule),
        )

        second = await ingest_pack(session, pack=charter)

        assert (
            await _count(session, PolicyPack),
            await _count(session, PolicySourceDocument),
            await _count(session, PolicyClause),
            await _count(session, PolicyRule),
        ) == counts
        assert first.pack_created is True
        assert second.pack_created is False
        assert second.rules_created == 0
        assert second.clauses_created == 0

    async def test_the_authoritative_pack_row_is_updated_in_place(self, session, charter):
        await ingest_pack(session, pack=charter)
        first_id = (await session.execute(select(PolicyPack.id))).scalars().one()

        relabelled = charter.model_copy(update={"ui_label": "RELABELLED FOR TEST"})
        await ingest_pack(session, pack=relabelled)

        row = (await session.execute(select(PolicyPack))).scalars().one()
        assert row.id == first_id
        assert row.ui_label == "RELABELLED FOR TEST"

    async def test_a_rule_removed_from_the_pack_is_pruned_not_left_behind(self, session, charter):
        """The rows mirror the pack. A stale rule would be a citable rule nobody authored."""
        await ingest_pack(session, pack=charter)
        trimmed = charter.model_copy(update={"rules": charter.rules[:-1]})

        report = await ingest_pack(session, pack=trimmed)

        assert report.rules_pruned == 1
        assert await _count(session, PolicyRule) == len(charter.rules) - 1

    async def test_extracted_clause_text_is_not_overwritten_by_a_re_ingest(self, session, charter):
        await ingest_pack(session, pack=charter)
        row = (await session.execute(select(PolicyClause))).scalars().first()
        row.text_content = "The operating carrier shall provide refreshments."
        row.extraction_method = "attachment_text_extraction"
        await session.flush()

        await ingest_pack(session, pack=charter)

        await session.refresh(row)
        assert row.text_content == "The operating carrier shall provide refreshments."
        assert row.extraction_method == "attachment_text_extraction"


class TestIngestionFailsClosed:
    async def test_ingestion_never_promotes_the_pack(self, session, charter):
        """The charter pack is dated guidance before ingestion and after it."""
        report = await ingest_pack(session, pack=charter)

        row = (await session.execute(select(PolicyPack))).scalars().one()
        assert row.status == PolicyPackStatus.official_guidance_dated
        assert row.verified_mode_eligible is False
        assert row.reviewed_by is None
        assert row.reviewed_at is None
        assert report.verified_mode_eligible is False
        assert report.status == PolicyPackStatus.official_guidance_dated.value

    async def test_an_unapproved_pack_cannot_be_ingested_as_verified(self, session, tmp_path):
        """Marking a pack eligible without a reviewer is refused by the loader, not by us.

        Ingestion is downstream of the ladder on purpose: there is no path where a pack that
        `POLICY_MODE=verified` would reject becomes a verified row because a write succeeded.
        """
        root = _copy_pack(tmp_path)
        _rewrite(
            root / CHARTER_ID / CHARTER_VERSION / "pack.yaml",
            status="approved",
            verified_mode_eligible=True,
        )

        with pytest.raises(PolicyPackUnavailable, match="without a recorded reviewer"):
            load_pack(
                pack_dir=root,
                pack_id=CHARTER_ID,
                version=CHARTER_VERSION,
                mode=PolicyMode.verified,
            )
        assert await _count(session, PolicyPack) == 0

    async def test_a_demo_fixture_pack_is_refused(self, session, charter):
        fictional = charter.model_copy(update={"demo_fixture": True})

        with pytest.raises(PolicyPackUnavailable, match="nothing to cite"):
            await ingest_pack(session, pack=fictional)

        assert await _count(session, PolicyPack) == 0

    async def test_an_archived_document_whose_hash_disagrees_is_refused(self, session, tmp_path):
        root = _copy_pack(tmp_path)
        pack_dir = root / CHARTER_ID / CHARTER_VERSION
        (pack_dir / "source.pdf").write_bytes(b"%PDF-1.7 archived original")
        _rewrite(
            pack_dir / "source-metadata.yaml",
            archived=True,
            content_sha256="0" * 64,
            local_path="source.pdf",
        )
        pack = load_pack(
            pack_dir=root, pack_id=CHARTER_ID, version=CHARTER_VERSION, mode=PolicyMode.charter
        )

        with pytest.raises(PolicyPackUnavailable, match=r"could not be verified"):
            await ingest_pack(session, pack=pack)

        assert await _count(session, PolicySourceDocument) == 0
        assert pack.source_document_verified is False
        assert "hashes to" in (pack.source_integrity_reason or "")

    async def test_the_refusal_carries_stream_bs_reason_and_reason_code(self, session, tmp_path):
        """The message a reader sees is B's finding, not a restatement of it."""
        root = _copy_pack(tmp_path)
        pack_dir = root / CHARTER_ID / CHARTER_VERSION
        _rewrite(
            pack_dir / "source-metadata.yaml",
            archived=True,
            content_sha256="not-sha256-hex",
            local_path="source.pdf",
        )
        pack = load_pack(
            pack_dir=root, pack_id=CHARTER_ID, version=CHARTER_VERSION, mode=PolicyMode.charter
        )

        with pytest.raises(PolicyPackUnavailable) as caught:
            await ingest_pack(session, pack=pack)

        assert caught.value.details["reason_code"] == REASON_SOURCE_DOCUMENT_UNVERIFIED
        assert caught.value.details["detail"] == pack.source_integrity_reason

    async def test_an_archived_document_that_matches_its_hash_is_stored(self, session, tmp_path):
        root = _copy_pack(tmp_path)
        pack_dir = root / CHARTER_ID / CHARTER_VERSION
        payload = b"%PDF-1.7 archived original"
        (pack_dir / "source.pdf").write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        _rewrite(
            pack_dir / "source-metadata.yaml",
            archived=True,
            content_sha256=digest,
            local_path="source.pdf",
        )
        pack = load_pack(
            pack_dir=root, pack_id=CHARTER_ID, version=CHARTER_VERSION, mode=PolicyMode.charter
        )

        await ingest_pack(session, pack=pack)

        document = (await session.execute(select(PolicySourceDocument))).scalars().one()
        assert document.content_hash == digest

    async def test_an_archived_claim_with_a_missing_file_is_refused(self, session, tmp_path):
        root = _copy_pack(tmp_path)
        _rewrite(
            root / CHARTER_ID / CHARTER_VERSION / "source-metadata.yaml",
            archived=True,
            content_sha256="0" * 64,
            local_path="source.pdf",
        )
        pack = load_pack(
            pack_dir=root, pack_id=CHARTER_ID, version=CHARTER_VERSION, mode=PolicyMode.charter
        )

        with pytest.raises(PolicyPackUnavailable, match="not present"):
            await ingest_pack(session, pack=pack)


class TestThereIsExactlyOneSourceVerificationPath:
    """Phase 4 G3 is Stream B's, and this proves ingestion did not grow a second opinion.

    `loader.verify_source_document` runs once inside `load_pack`. Ingestion reads the verdict it
    recorded and nothing else — it never opens the document and never computes a hash. Asserted
    behaviourally *and* structurally, because a comment saying so would not survive a refactor.
    """

    async def test_ingestion_trusts_a_failed_verdict_without_re_verifying(self, session, tmp_path):
        """The document on disk genuinely matches; only B's verdict says otherwise.

        A second implementation would hash the file, find it correct, and ingest. Refusing proves
        the verdict is the input, not the file.
        """
        root = _copy_pack(tmp_path)
        pack_dir = root / CHARTER_ID / CHARTER_VERSION
        payload = b"%PDF-1.7 archived original"
        (pack_dir / "source.pdf").write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        _rewrite(
            pack_dir / "source-metadata.yaml",
            archived=True,
            content_sha256=digest,
            local_path="source.pdf",
        )
        pack = load_pack(
            pack_dir=root, pack_id=CHARTER_ID, version=CHARTER_VERSION, mode=PolicyMode.charter
        )
        assert pack.source_document_verified is True

        overruled = pack.model_copy(
            update={
                "source_document_verified": False,
                "source_integrity_reason": "Stream B declined it",
            }
        )
        with pytest.raises(PolicyPackUnavailable, match="Stream B declined it"):
            await ingest_pack(session, pack=overruled)

    async def test_ingestion_does_not_overrule_a_passing_verdict_either(self, session, tmp_path):
        """Deference runs both ways, or it is not deference.

        Here the recorded hash does not match the file, and B's verdict is forced to pass. A
        second verifier in this layer would refuse; a single authority means the verdict decides.
        """
        root = _copy_pack(tmp_path)
        pack_dir = root / CHARTER_ID / CHARTER_VERSION
        (pack_dir / "source.pdf").write_bytes(b"%PDF-1.7 archived original")
        _rewrite(
            pack_dir / "source-metadata.yaml",
            archived=True,
            content_sha256="0" * 64,
            local_path="source.pdf",
        )
        pack = load_pack(
            pack_dir=root, pack_id=CHARTER_ID, version=CHARTER_VERSION, mode=PolicyMode.charter
        )
        assert pack.source_document_verified is False

        trusted = pack.model_copy(
            update={"source_document_verified": True, "source_integrity_reason": None}
        )
        await ingest_pack(session, pack=trusted)

        document = (await session.execute(select(PolicySourceDocument))).scalars().one()
        assert document.content_hash == "0" * 64

    def test_the_data_layer_computes_no_content_hash(self):
        """Structural guard: the duplicate cannot creep back in unnoticed."""
        from pathlib import Path as _Path

        import app.db.policy_ingest as ingest_module

        source = _Path(ingest_module.__file__).read_text(encoding="utf-8")
        body = "\n".join(line for line in source.splitlines() if not line.strip().startswith("#"))
        assert "hashlib" not in body
        assert "sha256(" not in body
        assert "read_bytes" not in body

    def test_verify_source_document_is_defined_once_in_the_application(self):
        from pathlib import Path as _Path

        import app

        definitions = sorted(
            path.relative_to(_Path(app.__file__).parent).as_posix()
            for path in _Path(app.__file__).parent.rglob("*.py")
            if "def verify_source_document(" in path.read_text(encoding="utf-8")
        )

        assert definitions == ["policy/loader.py"]

    def test_the_placeholder_and_reason_code_are_the_loaders_own(self):
        """Imported, not restated, so the two layers cannot drift on a spelling."""
        from app.db import policy_ingest
        from app.policy import loader

        assert policy_ingest.PENDING_ARCHIVAL is loader.PENDING_ARCHIVAL
        assert (
            policy_ingest.REASON_SOURCE_DOCUMENT_UNVERIFIED
            is loader.REASON_SOURCE_DOCUMENT_UNVERIFIED
        )


class TestTheDecisionRecord:
    async def test_every_candidate_row_carries_the_resolver_hash_and_version(
        self, session, charter
    ):
        await ingest_pack(session, pack=charter)
        resolution = resolve_select(trip_context=TRIP_CONTEXT, packs=[charter])

        record = await record_resolution(
            session,
            incident_id=1,
            resolution=resolution,
            packs=[charter],
            trip_context=TRIP_CONTEXT,
        )

        rows = (await session.execute(select(PolicyApplicability))).scalars().all()
        assert len(rows) == len(resolution.candidates)
        assert {row.resolver_hash for row in rows} == {record.resolver_hash}
        assert {row.resolver_version for row in rows} == {resolution.resolver_version}
        assert len(record.resolver_hash) == 32

    async def test_the_persisted_hash_matches_a_recomputation_of_the_same_resolution(
        self, session, charter
    ):
        """The stored value is reproducible, which is what makes it a replay anchor."""
        from app.db.policy_identity import compute_resolver_hash

        await ingest_pack(session, pack=charter)
        resolution = resolve_select(trip_context=TRIP_CONTEXT, packs=[charter])
        await record_resolution(
            session,
            incident_id=1,
            resolution=resolution,
            packs=[charter],
            trip_context=TRIP_CONTEXT,
        )

        stored = (await session.execute(select(PolicyApplicability.resolver_hash))).scalars().one()

        assert stored == compute_resolver_hash(
            resolution=resolve_select(trip_context=TRIP_CONTEXT, packs=[charter]),
            trip_context=TRIP_CONTEXT,
            packs=[charter],
        )

    async def test_the_applicability_status_and_basis_are_recorded(self, session, charter):
        await ingest_pack(session, pack=charter)
        resolution = resolve_select(trip_context=TRIP_CONTEXT, packs=[charter])

        await record_resolution(
            session,
            incident_id=1,
            resolution=resolution,
            packs=[charter],
            trip_context=TRIP_CONTEXT,
        )

        row = (await session.execute(select(PolicyApplicability))).scalars().one()
        assert row.status == ApplicabilityStatus.applicable
        assert row.basis == {"itinerary.origin_country": "IN"}
        assert row.required_facts == list(charter.required_facts)
        assert row.conflict_disposition["decision"] == resolution.decision

    async def test_a_resolution_against_an_uningested_pack_is_refused(self, session, charter):
        resolution = resolve_select(trip_context=TRIP_CONTEXT, packs=[charter])

        with pytest.raises(PolicyPackUnavailable, match="has not been ingested"):
            await record_resolution(
                session,
                incident_id=1,
                resolution=resolution,
                packs=[charter],
                trip_context=TRIP_CONTEXT,
            )

    async def test_an_entitlement_is_reproducible_from_the_row_alone(self, session, charter):
        from app.policy.entitlements import calculate

        await ingest_pack(session, pack=charter)
        resolution = resolve_select(trip_context=TRIP_CONTEXT, packs=[charter])
        record = await record_resolution(
            session,
            incident_id=1,
            resolution=resolution,
            packs=[charter],
            trip_context=TRIP_CONTEXT,
        )
        cited = calculate(facts=TRIP_CONTEXT, pack=charter, resolve_applicability=False)
        assert cited.rules_fired, "the delay context should fire at least one rule"

        rows = await record_entitlement_evaluation(
            session,
            incident_id=1,
            applicability=record.applicability[0],
            cited=cited,
            trip_context=TRIP_CONTEXT,
        )

        assert len(rows) == len(cited.rules_fired)
        stored = (await session.execute(select(EntitlementEvaluation))).scalars().first()
        assert stored.result["pack_hash"] == charter.pack_hash
        assert stored.result["pack_version"] == CHARTER_VERSION
        assert stored.input_facts == TRIP_CONTEXT
        assert stored.applicability_id == record.applicability[0].id

    async def test_a_fired_rule_that_is_not_ingested_is_refused(self, session, charter):
        from app.policy.entitlements import calculate

        await ingest_pack(session, pack=charter)
        resolution = resolve_select(trip_context=TRIP_CONTEXT, packs=[charter])
        record = await record_resolution(
            session,
            incident_id=1,
            resolution=resolution,
            packs=[charter],
            trip_context=TRIP_CONTEXT,
        )
        cited = calculate(facts=TRIP_CONTEXT, pack=charter, resolve_applicability=False)
        invented = cited.model_copy(update={"rules_fired": ["rule.nobody.authored"]})

        with pytest.raises(PolicyPackUnavailable, match="not ingested"):
            await record_entitlement_evaluation(
                session,
                incident_id=1,
                applicability=record.applicability[0],
                cited=invented,
                trip_context=TRIP_CONTEXT,
            )
