"""Policy ingestion against real Postgres — Phase 4 G5 + G6.

`test_policy_ingest.py` proves the ingestion logic on SQLite. It cannot prove the two properties
that decide whether a persisted decision is trustworthy: the unique constraints that make
re-ingestion an update rather than a duplicate, and the foreign keys that stop an applicability
row pointing at an incident or a pack that does not exist. SQLite enforces neither unless asked,
so both are asserted here against the engine the demo runs on.

`TRAVELOPS_TEST_DATABASE_URL` opts in, matching the existing real-database tests. Migrations are
expected to have been applied; these tests own rows, not schema.

Owner: Stream C.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.db.policy_identity import compute_resolver_hash
from app.db.policy_ingest import (
    ingest_active_pack,
    ingest_pack,
    record_entitlement_evaluation,
    record_resolution,
)
from app.models.policy import (
    EntitlementEvaluation,
    PolicyApplicability,
    PolicyClause,
    PolicyPack,
    PolicyRule,
    PolicySourceDocument,
)
from app.policy.entitlements import calculate
from app.policy.resolver import select as resolve_select
from tests.contract.postgres_support import requires_postgres
from tests.unit.db.conftest import CHARTER_ID, CHARTER_VERSION

pytestmark = [requires_postgres]

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


class TestIngestionAgainstRealPostgres:
    async def test_the_committed_pack_ingests(self, pg_sessionmaker, pg_policy_tables, charter):
        async with pg_sessionmaker() as session:
            report = await ingest_pack(session, pack=charter)
            await session.commit()

        async with pg_sessionmaker() as session:
            assert await _count(session, PolicyPack) == 1
            assert await _count(session, PolicySourceDocument) == 1
            assert await _count(session, PolicyRule) == len(charter.rules)
            assert await _count(session, PolicyClause) == report.clauses_created

    async def test_the_active_pack_ingests_through_the_configured_mode(
        self, pg_sessionmaker, pg_policy_tables
    ):
        """Routed through Stream B's `load_active_pack`, so POLICY_MODE still decides."""
        get_settings.cache_clear()
        try:
            async with pg_sessionmaker() as session:
                report = await ingest_active_pack(session)
                await session.commit()
        finally:
            get_settings.cache_clear()

        assert report.pack_key == CHARTER_ID
        assert report.version == CHARTER_VERSION
        assert report.verified_mode_eligible is False

    async def test_re_ingesting_in_a_second_transaction_creates_no_duplicates(
        self, pg_sessionmaker, pg_policy_tables, charter
    ):
        """The idempotency that matters: two committed transactions, not two calls in one."""
        async with pg_sessionmaker() as session:
            await ingest_pack(session, pack=charter)
            await session.commit()
        async with pg_sessionmaker() as session:
            before = (
                await _count(session, PolicyPack),
                await _count(session, PolicyRule),
                await _count(session, PolicyClause),
            )

        async with pg_sessionmaker() as session:
            second = await ingest_pack(session, pack=charter)
            await session.commit()

        async with pg_sessionmaker() as session:
            after = (
                await _count(session, PolicyPack),
                await _count(session, PolicyRule),
                await _count(session, PolicyClause),
            )
        assert after == before
        assert second.pack_created is False
        assert second.rules_created == 0
        assert second.clauses_created == 0

    async def test_the_pack_unique_constraint_is_real(
        self, pg_sessionmaker, pg_policy_tables, charter
    ):
        """Idempotency is enforced by the schema, not only by our select-then-update."""
        async with pg_sessionmaker() as session:
            await ingest_pack(session, pack=charter)
            await session.commit()

        with pytest.raises(IntegrityError):
            async with pg_sessionmaker() as session:
                session.add(
                    PolicyPack(
                        pack_key=CHARTER_ID,
                        version=CHARTER_VERSION,
                        jurisdiction="IN",
                        authority="duplicate",
                        status=charter.status,
                        verified_mode_eligible=False,
                        ui_label="duplicate",
                        pack_hash=charter.pack_hash,
                    )
                )
                await session.commit()

    async def test_a_changed_pack_updates_its_row_rather_than_adding_one(
        self, pg_sessionmaker, pg_policy_tables, charter
    ):
        async with pg_sessionmaker() as session:
            await ingest_pack(session, pack=charter)
            await session.commit()

        edited = charter.model_copy(update={"pack_hash": "f" * 16})
        async with pg_sessionmaker() as session:
            await ingest_pack(session, pack=edited)
            await session.commit()

        async with pg_sessionmaker() as session:
            rows = (await session.execute(select(PolicyPack))).scalars().all()
        assert len(rows) == 1
        assert rows[0].pack_hash == "f" * 16

    async def test_ingestion_leaves_the_charter_pack_unpromoted(
        self, pg_sessionmaker, pg_policy_tables, charter
    ):
        """A successful write must never be mistaken for a verified pack."""
        async with pg_sessionmaker() as session:
            await ingest_pack(session, pack=charter)
            await session.commit()

        async with pg_sessionmaker() as session:
            row = (await session.execute(select(PolicyPack))).scalars().one()

        assert row.status == "official_guidance_dated"
        assert row.verified_mode_eligible is False
        assert row.reviewed_by is None


class TestTheDecisionRecordAgainstRealPostgres:
    async def test_resolver_hash_is_persisted_and_reproducible(
        self, pg_sessionmaker, pg_policy_tables, charter, pg_incident
    ):
        resolution = resolve_select(trip_context=TRIP_CONTEXT, packs=[charter])

        async with pg_sessionmaker() as session:
            await ingest_pack(session, pack=charter)
            record = await record_resolution(
                session,
                incident_id=pg_incident,
                resolution=resolution,
                packs=[charter],
                trip_context=TRIP_CONTEXT,
            )
            await session.commit()
            expected = record.resolver_hash

        async with pg_sessionmaker() as session:
            stored = (
                (await session.execute(select(PolicyApplicability.resolver_hash))).scalars().all()
            )

        assert stored == [expected]
        assert expected == compute_resolver_hash(
            resolution=resolve_select(trip_context=TRIP_CONTEXT, packs=[charter]),
            trip_context=TRIP_CONTEXT,
            packs=[charter],
        )

    async def test_an_entitlement_is_reproducible_from_the_database_alone(
        self, pg_sessionmaker, pg_policy_tables, charter, pg_incident
    ):
        """Phase 4 G6's definition of done, asserted by reading back only rows."""
        cited = calculate(facts=TRIP_CONTEXT, pack=charter, resolve_applicability=False)
        assert cited.rules_fired, "the delay context should fire at least one rule"
        resolution = resolve_select(trip_context=TRIP_CONTEXT, packs=[charter])

        async with pg_sessionmaker() as session:
            await ingest_pack(session, pack=charter)
            record = await record_resolution(
                session,
                incident_id=pg_incident,
                resolution=resolution,
                packs=[charter],
                trip_context=TRIP_CONTEXT,
            )
            await record_entitlement_evaluation(
                session,
                incident_id=pg_incident,
                applicability=record.applicability[0],
                cited=cited,
                trip_context=TRIP_CONTEXT,
            )
            await session.commit()

        async with pg_sessionmaker() as session:
            rows = (
                await session.execute(
                    select(EntitlementEvaluation, PolicyRule, PolicyPack)
                    .join(PolicyRule, PolicyRule.id == EntitlementEvaluation.policy_rule_id)
                    .join(PolicyPack, PolicyPack.id == EntitlementEvaluation.policy_pack_id)
                    .order_by(EntitlementEvaluation.id)
                )
            ).all()
            clause_refs = set(
                (await session.execute(select(PolicyClause.clause_ref))).scalars().all()
            )

        assert rows, "at least one rule fired and must be recorded"
        for evaluation, rule, pack_row in rows:
            # Pack identity, the rule that fired, and its clause references — all from rows.
            assert (pack_row.pack_key, pack_row.version) == (CHARTER_ID, CHARTER_VERSION)
            assert pack_row.pack_hash == charter.pack_hash
            assert rule.rule_key in cited.rules_fired
            assert set(rule.source_clause_refs) <= clause_refs
            assert evaluation.input_facts == TRIP_CONTEXT
            assert evaluation.result["pack_hash"] == charter.pack_hash

    async def test_an_applicability_row_cannot_reference_a_missing_incident(
        self, pg_sessionmaker, pg_policy_tables, charter
    ):
        """The foreign key SQLite would have let through."""
        resolution = resolve_select(trip_context=TRIP_CONTEXT, packs=[charter])

        with pytest.raises(IntegrityError):
            async with pg_sessionmaker() as session:
                await ingest_pack(session, pack=charter)
                await record_resolution(
                    session,
                    incident_id=9_999_999,
                    resolution=resolution,
                    packs=[charter],
                    trip_context=TRIP_CONTEXT,
                )
                await session.commit()

    async def test_pruning_a_cited_rule_cannot_orphan_an_evaluation(
        self, pg_sessionmaker, pg_policy_tables, charter, pg_incident
    ):
        """A recorded citation outlives a pack edit, or the record was never trustworthy.

        Postgres refuses the prune while an evaluation still references the rule, which is the
        outcome we want: the fix is a new pack version, not a silently rewritten history.
        """
        cited = calculate(facts=TRIP_CONTEXT, pack=charter, resolve_applicability=False)
        fired = str(cited.rules_fired[0])
        resolution = resolve_select(trip_context=TRIP_CONTEXT, packs=[charter])

        async with pg_sessionmaker() as session:
            await ingest_pack(session, pack=charter)
            record = await record_resolution(
                session,
                incident_id=pg_incident,
                resolution=resolution,
                packs=[charter],
                trip_context=TRIP_CONTEXT,
            )
            await record_entitlement_evaluation(
                session,
                incident_id=pg_incident,
                applicability=record.applicability[0],
                cited=cited,
                trip_context=TRIP_CONTEXT,
            )
            await session.commit()

        trimmed = charter.model_copy(
            update={"rules": [rule for rule in charter.rules if rule.id != fired]}
        )
        with pytest.raises(IntegrityError):
            async with pg_sessionmaker() as session:
                await ingest_pack(session, pack=trimmed)
                await session.commit()
