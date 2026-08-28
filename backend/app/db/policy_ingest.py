"""Policy pack → database ingestion — STREAM C.

Phase 4 G6 in `docs/38-phase4-verified-policy.md`. Every table written here has existed since
the initial migration with nothing populating it; the pack has been read from YAML on every
call. That works until you need to answer "which clause, in which document revision, produced
this figure" from the record alone — after a pack directory has moved, been re-versioned, or
been replaced by the verified pack.

## The pack stays the source of truth

This is a **projection, not a second definition**. Rules are parsed only by Stream B's
`load_pack`, which is what enforces the status ladder, the clause-reference requirement and the
superseded-rule exclusion. Nothing here re-parses YAML, re-validates a rule or decides what a
rule means. `app/policy/*` continues to evaluate from `LoadedPack`; these rows exist so a
recorded decision remains explicable, not so anything reads policy from the database instead.

Two consequences worth stating, because both are easy to get wrong:

* **Ingestion never promotes.** `status`, `verified_mode_eligible`, `reviewed_by` and
  `reviewed_at` are copied verbatim from the pack and its `review.yaml`. A successful ingest of
  the charter pack leaves it `official_guidance_dated` with `verified_mode_eligible = false`,
  exactly as loaded. Whether verified mode may run is decided by `POLICY_MODE` and B's loader,
  and nothing in this module can widen that.
* **Clause text is never invented.** The charter's own `source-metadata.yaml` says the full
  text must be read from the archived original, and that original is not yet committed. So a
  clause row records the *reference* with `extraction_method='reference_only'` and a text
  placeholder that names itself as unavailable. When extraction lands, re-ingesting fills the
  text in and leaves already-extracted text alone.

## Idempotency

Re-ingesting the same pack updates in place and prunes anything the pack no longer declares, so
the rows are a mirror of the pack rather than an accumulating log. Keys are the ones the schema
already declares unique: `(pack_key, version)`, `(policy_pack_id, rule_key)` and
`(source_document_id, clause_ref)`. `source-metadata.yaml` describes exactly one document, so
the pack's single source-document row is matched by pack rather than by URL — changing the URL
of the same document must update that row, not add a second one.

## Source-document verification is not done here

Phase 4 G3 is Stream B's, and `loader.verify_source_document` is the **single authority**. It runs
once inside `load_pack` and records its verdict on the pack. This module reads that verdict in
`_content_hash_to_persist` and refuses to store a hash it failed; it never opens the document and
never computes a content hash. `PENDING_ARCHIVAL` and the reason code are imported from the loader
rather than restated, so there is one spelling of each.

Owner: Stream C. Reads Stream B's loader output; writes Stream C's tables.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.policy_identity import compute_resolver_hash
from app.errors import PolicyPackUnavailable
from app.models.policy import (
    EntitlementEvaluation,
    PolicyApplicability,
    PolicyClause,
    PolicyPack,
    PolicyRule,
    PolicySourceDocument,
)
from app.policy.loader import PENDING_ARCHIVAL, REASON_SOURCE_DOCUMENT_UNVERIFIED

#: Recorded as a clause's text when the archived primary document has not been extracted. It is
#: deliberately not empty and deliberately not a paraphrase: `policy_clause.text` is NOT NULL,
#: and putting `rules.yaml`'s interpretation here would file our own wording as the regulator's.
CLAUSE_TEXT_UNAVAILABLE = (
    "UNAVAILABLE: clause text is not extracted. Read the archived primary document named by "
    "the pack's source metadata."
)

#: `policy_clause.extraction_method` for a reference recorded without its text.
EXTRACTION_REFERENCE_ONLY = "reference_only"

#: `policy_rule.event_type` when a rule's `when` clause does not pin one. Not a guess: it
#: records that the rule is not scoped to a single event type.
EVENT_TYPE_ANY = "any"

_EVENT_TYPE_FACT = "event.type"


@dataclass
class PackIngestReport:
    """What one ingest did. Counted rather than logged, so a test can assert idempotency."""

    pack_key: str
    version: str
    pack_hash: str
    status: str
    verified_mode_eligible: bool
    pack_created: bool = False
    documents: int = 0
    clauses_created: int = 0
    clauses_updated: int = 0
    clauses_pruned: int = 0
    rules_created: int = 0
    rules_updated: int = 0
    rules_pruned: int = 0

    def summary(self) -> str:
        return (
            f"ingested {self.pack_key}@{self.version} hash={self.pack_hash} "
            f"status={self.status} verified_eligible={self.verified_mode_eligible} "
            f"rules +{self.rules_created}/~{self.rules_updated}/-{self.rules_pruned} "
            f"clauses +{self.clauses_created}/~{self.clauses_updated}/-{self.clauses_pruned}"
        )


@dataclass
class DecisionRecord:
    """The persisted trace of one policy decision for one incident."""

    applicability: list[PolicyApplicability] = field(default_factory=list)
    evaluations: list[EntitlementEvaluation] = field(default_factory=list)
    resolver_hash: str = ""


# ------------------------------------------------------------------------------ pack ingestion


def _iso_date(value: Any) -> date | None:
    """Parse only an unambiguous ISO date. `2019-02` is a month, and a month is not a date.

    Returning None for a partial date keeps `effective_from` honest: the charter records
    `document_date: 2019-02`, and inventing the first of the month would put a precise
    effective date on the record that no source states.
    """
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value or "").strip()
    if len(text) != 10:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _iso_datetime(value: Any) -> datetime | None:
    """Parse a timestamp, always returning it timezone-aware.

    `retrieved_at` and `reviewed_at` are `DateTime(timezone=True)`. A pack records a plain date
    (`received_at: 2026-08-20`), and storing that naive would leave the instant dependent on the
    server's session timezone — so a retrieval date could read back as the day before.
    """
    parsed: datetime | None = None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day)
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            only_date = _iso_date(text)
            parsed = datetime(only_date.year, only_date.month, only_date.day) if only_date else None
    if parsed is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _event_type_of(rule: Any) -> str:
    """The event type a rule's own `when` clause pins, or `any`.

    Walks the condition tree for an `event.type` equality leaf. Derived from the rule rather
    than mapped by hand, so a new rule classifies itself; `EVENT_TYPE_ANY` when the rule is not
    scoped to one event. This reads a condition, it never evaluates one.
    """

    def walk(node: Any) -> str | None:
        if isinstance(node, list):
            for item in node:
                found = walk(item)
                if found:
                    return found
            return None
        if not isinstance(node, Mapping):
            return None
        if node.get("fact") == _EVENT_TYPE_FACT and node.get("op") in (None, "eq"):
            value = node.get("value")
            if isinstance(value, str) and value:
                return value
        for key in ("all", "all_of", "any_of", "any", "not"):
            if key in node:
                found = walk(node[key])
                if found:
                    return found
        return None

    return walk(getattr(rule, "when", None)) or EVENT_TYPE_ANY


def _entitlement_document(rule: Any) -> dict[str, Any]:
    """A rule's payload: its entitlement, or its effect, or an empty mapping.

    `policy_rule.entitlement_json` is NOT NULL and a suppression rule has an `effect` instead of
    an `entitlement`, so both are recorded under the key that says which one it was.
    """
    entitlement = getattr(rule, "entitlement", None)
    effect = getattr(rule, "effect", None)
    document: dict[str, Any] = {}
    if entitlement:
        document["entitlement"] = dict(entitlement)
    if effect:
        document["effect"] = dict(effect)
    return document


def _content_hash_to_persist(pack: Any) -> str:
    """The content hash to store, refusing one Stream B could not verify.

    **This performs no verification.** `loader.verify_source_document` is the single authority and
    it has already run once, inside `load_pack`, recording its verdict on the pack as
    `source_archived`, `source_content_sha256`, `source_document_verified` and
    `source_integrity_reason`. Reading that verdict is the whole of this function; re-hashing the
    file here would be a second opinion about the same question, which is the duplication the data
    layer already warns about in `plan_identity.approval_covers`.

    Three outcomes, and the middle one is why this function exists at all:

    * nothing archived — the recorded value (normally `PENDING_ARCHIVAL`) is stored as written. A
      dated pack must stay ingestible; its unarchived PDF is exactly why it is not verified.
    * archived and verified — the hash is stored.
    * archived and **not** verified — refused. B's `_reject_for_source_integrity` deliberately lets
      a dated pack load with a failed verdict so it can report the truth about its source, so this
      is the only thing standing between that verdict and a false provenance claim in the record.
    """
    recorded = str(getattr(pack, "source_content_sha256", None) or PENDING_ARCHIVAL)
    if not getattr(pack, "source_archived", False):
        return recorded

    if not getattr(pack, "source_document_verified", False):
        reason = getattr(pack, "source_integrity_reason", None)
        pack_ref = f"policy pack {getattr(pack, 'pack_id', '?')}@{getattr(pack, 'version', '?')}"
        raise PolicyPackUnavailable(
            f"{pack_ref} claims an archived source document that could not be verified: {reason}",
            details={"reason_code": REASON_SOURCE_DOCUMENT_UNVERIFIED, "detail": reason},
        )
    return recorded.lower()


async def ingest_pack(session: AsyncSession, *, pack: Any) -> PackIngestReport:
    """Project one already-loaded pack into the policy tables. Idempotent.

    `pack` must come from `app.policy.loader.load_pack` (or `entitlements.load_active_pack`),
    because that is where the status ladder and the clause-reference requirement are enforced.
    Runs inside the caller's transaction and does not commit, so a partial projection cannot be
    committed on its own.

    Refuses a demo fixture: `citations_permitted` is false for one, and these tables are the
    citation record. A fictional pack belongs in the engine's inputs, never in the provenance
    the UI cites.
    """
    if not getattr(pack, "citations_permitted", True):
        raise PolicyPackUnavailable(
            f"policy pack {pack.pack_id}@{pack.version} is a demo fixture and has nothing to "
            "cite, so it is not ingested into the policy record",
            details={"reason_code": "POLICY_PACK_UNAVAILABLE", "demo_fixture": True},
        )

    content_hash = _content_hash_to_persist(pack)
    status_value = getattr(pack.status, "value", pack.status)
    review: Mapping[str, Any] = getattr(pack, "review", None) or {}

    report = PackIngestReport(
        pack_key=pack.pack_id,
        version=pack.version,
        pack_hash=pack.pack_hash,
        status=str(status_value),
        verified_mode_eligible=bool(pack.verified_mode_eligible),
    )

    row = (
        (
            await session.execute(
                select(PolicyPack).where(
                    PolicyPack.pack_key == pack.pack_id, PolicyPack.version == pack.version
                )
            )
        )
        .scalars()
        .first()
    )
    if row is None:
        row = PolicyPack(pack_key=pack.pack_id, version=pack.version)
        session.add(row)
        report.pack_created = True

    row.jurisdiction = pack.jurisdiction
    row.authority = pack.authority
    row.document = pack.document
    row.effective_from = _iso_date(getattr(pack, "document_date", None))
    row.currency = pack.currency
    # Copied, never computed. See the module docstring: ingestion does not promote a pack.
    row.status = pack.status
    row.verified_mode_eligible = bool(pack.verified_mode_eligible)
    row.ui_label = pack.ui_label
    row.reviewed_by = review.get("reviewer_name")
    row.reviewed_at = _iso_datetime(review.get("reviewed_at"))
    row.pack_hash = pack.pack_hash
    await session.flush()

    document = await _upsert_source_document(
        session, pack=pack, pack_row=row, content_hash=content_hash
    )
    report.documents = 1

    await _sync_clauses(session, pack=pack, document=document, report=report)
    await _sync_rules(session, pack=pack, pack_row=row, report=report)
    await session.flush()
    return report


async def ingest_active_pack(session: AsyncSession, *, settings: Any = None) -> PackIngestReport:
    """Ingest whichever pack the running configuration resolves to.

    Deliberately routed through Stream B's `load_active_pack`, so `POLICY_MODE` decides what may
    be loaded and this module never reaches for a pack the mode would have refused.
    """
    from app.policy.entitlements import load_active_pack

    return await ingest_pack(session, pack=load_active_pack(settings))


async def _upsert_source_document(
    session: AsyncSession, *, pack: Any, pack_row: PolicyPack, content_hash: str
) -> PolicySourceDocument:
    source: Mapping[str, Any] = getattr(pack, "source", None) or {}
    document = (
        (
            await session.execute(
                select(PolicySourceDocument)
                .where(PolicySourceDocument.policy_pack_id == pack_row.id)
                .order_by(PolicySourceDocument.id)
            )
        )
        .scalars()
        .first()
    )
    if document is None:
        document = PolicySourceDocument(policy_pack_id=pack_row.id)
        session.add(document)

    licence = source.get("redistribution") or {}
    document.title = str(source.get("title") or pack.document or pack.pack_id)
    document.source_url = str(source.get("official_url") or "")
    document.published_revision = (
        str(source.get("published")) if source.get("published") is not None else None
    )
    document.retrieved_at = _iso_datetime(source.get("received_at"))
    document.content_hash = content_hash
    document.licence_note = (
        str(licence.get("status"))
        if isinstance(licence, Mapping) and licence.get("status")
        else None
    )
    document.local_path = (
        str(source.get("local_path")) if source.get("local_path") is not None else None
    )
    await session.flush()
    return document


def _clause_refs(pack: Any) -> list[str]:
    """Every clause the pack's rules cite, deduplicated, in a stable order.

    Taken from the rules rather than from a separate list, so a citation cannot exist in the
    record without a rule that makes it.
    """
    refs: list[str] = []
    for rule in getattr(pack, "rules", None) or []:
        for ref in getattr(rule, "source_clause_refs", None) or []:
            text = str(ref).strip()
            if text and text not in refs:
                refs.append(text)
    return sorted(refs)


async def _sync_clauses(
    session: AsyncSession, *, pack: Any, document: PolicySourceDocument, report: PackIngestReport
) -> None:
    wanted = _clause_refs(pack)
    existing = {
        row.clause_ref: row
        for row in (
            (
                await session.execute(
                    select(PolicyClause).where(PolicyClause.source_document_id == document.id)
                )
            )
            .scalars()
            .all()
        )
    }

    for ref in wanted:
        row = existing.get(ref)
        if row is None:
            session.add(
                PolicyClause(
                    source_document_id=document.id,
                    clause_ref=ref,
                    text_content=CLAUSE_TEXT_UNAVAILABLE,
                    extraction_method=EXTRACTION_REFERENCE_ONLY,
                )
            )
            report.clauses_created += 1
            continue
        # Already-extracted text is never overwritten by the placeholder: a re-ingest after
        # extraction must not throw the text away.
        if row.text_content == CLAUSE_TEXT_UNAVAILABLE:
            row.extraction_method = EXTRACTION_REFERENCE_ONLY
            report.clauses_updated += 1

    for ref, row in existing.items():
        if ref not in wanted:
            await session.delete(row)
            report.clauses_pruned += 1


async def _sync_rules(
    session: AsyncSession, *, pack: Any, pack_row: PolicyPack, report: PackIngestReport
) -> None:
    existing = {
        row.rule_key: row
        for row in (
            (
                await session.execute(
                    select(PolicyRule).where(PolicyRule.policy_pack_id == pack_row.id)
                )
            )
            .scalars()
            .all()
        )
    }
    wanted: set[str] = set()

    for rule in getattr(pack, "rules", None) or []:
        wanted.add(rule.id)
        row = existing.get(rule.id)
        if row is None:
            row = PolicyRule(policy_pack_id=pack_row.id, rule_key=rule.id)
            session.add(row)
            report.rules_created += 1
        else:
            report.rules_updated += 1

        row.event_type = _event_type_of(rule)
        row.condition_json = dict(getattr(rule, "when", None) or {})
        row.entitlement_json = _entitlement_document(rule)
        row.source_clause_refs = list(getattr(rule, "source_clause_refs", None) or [])
        row.review_status = rule.status
        row.excluded_from_evaluation = bool(rule.excluded_from_evaluation)
        row.interpretation = getattr(rule, "interpretation", None)

    for key, row in existing.items():
        if key not in wanted:
            await session.delete(row)
            report.rules_pruned += 1


# --------------------------------------------------------------------------- decision records


async def _pack_row(session: AsyncSession, *, pack_id: str, version: str) -> PolicyPack:
    row = (
        (
            await session.execute(
                select(PolicyPack).where(
                    PolicyPack.pack_key == pack_id, PolicyPack.version == version
                )
            )
        )
        .scalars()
        .first()
    )
    if row is None:
        raise PolicyPackUnavailable(
            f"policy pack {pack_id}@{version} has not been ingested, so a decision cannot be "
            "pinned to it",
            details={"reason_code": "POLICY_PACK_UNAVAILABLE"},
        )
    return row


async def record_resolution(
    session: AsyncSession,
    *,
    incident_id: int,
    resolution: Any,
    packs: Sequence[Any],
    trip_context: Mapping[str, Any] | None = None,
) -> DecisionRecord:
    """Persist one jurisdiction resolution: a row per candidate, carrying `resolver_hash`.

    The hash is computed once for the whole resolution and written to every candidate row,
    because it identifies the decision rather than any single candidate — the reason two packs
    overlapping resolves to `needs_human` is a property of the resolution as a whole.

    Every candidate is recorded, including `not_applicable` and `undetermined` ones. A resolver
    that considered a pack and ruled it out is evidence, and dropping it would leave the record
    unable to show why the selected pack was selected.
    """
    resolver_hash = compute_resolver_hash(
        resolution=resolution, trip_context=trip_context, packs=packs
    )
    record = DecisionRecord(resolver_hash=resolver_hash)
    conflicts = [str(item) for item in getattr(resolution, "conflicts", None) or []]
    blocking = [str(item) for item in getattr(resolution, "blocking_reasons", None) or []]

    for candidate in getattr(resolution, "candidates", None) or []:
        pack_row = await _pack_row(
            session, pack_id=candidate.pack_id, version=candidate.pack_version
        )
        row = PolicyApplicability(
            incident_id=incident_id,
            policy_pack_id=pack_row.id,
            status=candidate.status,
            basis=dict(getattr(candidate, "basis", None) or {}),
            required_facts=list(getattr(candidate, "required_facts", None) or []),
            missing_facts=list(getattr(candidate, "missing_facts", None) or []),
            evidence_refs=list(getattr(candidate, "evidence_refs", None) or []),
            conflict_disposition={
                "decision": str(getattr(resolution, "decision", "")),
                "conflicts": conflicts,
                "blocking_reasons": blocking,
                "on_conflict": next(
                    (
                        str(getattr(pack, "on_conflict", ""))
                        for pack in packs
                        if getattr(pack, "pack_id", None) == candidate.pack_id
                    ),
                    "",
                ),
            },
            resolver_version=str(getattr(resolution, "resolver_version", "")),
            resolver_hash=resolver_hash,
        )
        session.add(row)
        record.applicability.append(row)

    await session.flush()
    return record


async def record_entitlement_evaluation(
    session: AsyncSession,
    *,
    incident_id: int,
    applicability: PolicyApplicability,
    cited: Any,
    trip_context: Mapping[str, Any] | None = None,
) -> list[EntitlementEvaluation]:
    """Persist one evaluated entitlement, a row per rule that fired.

    `input_facts` is the trip context the engine was given and `result` is the whole
    `CitedEntitlement`, so the figure, its clause references, the pack hash and the reason codes
    are all recoverable from the row without re-running anything.

    A rule that fired but was never ingested is refused rather than skipped: a result citing a
    rule the record cannot resolve is exactly the untraceable figure this phase exists to
    prevent.
    """
    pack_row = await _pack_row(
        session,
        pack_id=str(getattr(cited, "pack_id", "")),
        version=str(getattr(cited, "pack_version", "")),
    )
    result = cited.model_dump(mode="json") if hasattr(cited, "model_dump") else dict(cited)
    rows: list[EntitlementEvaluation] = []

    for rule_key in list(getattr(cited, "rules_fired", None) or []):
        rule_row = (
            (
                await session.execute(
                    select(PolicyRule).where(
                        PolicyRule.policy_pack_id == pack_row.id,
                        PolicyRule.rule_key == str(rule_key),
                    )
                )
            )
            .scalars()
            .first()
        )
        if rule_row is None:
            raise PolicyPackUnavailable(
                f"rule '{rule_key}' fired but is not ingested for "
                f"{pack_row.pack_key}@{pack_row.version}",
                details={"reason_code": "POLICY_PACK_UNAVAILABLE", "rule": str(rule_key)},
            )
        row = EntitlementEvaluation(
            incident_id=incident_id,
            applicability_id=applicability.id,
            policy_pack_id=pack_row.id,
            policy_rule_id=rule_row.id,
            input_facts=dict(trip_context or {}),
            result=result,
        )
        session.add(row)
        rows.append(row)

    await session.flush()
    return rows
