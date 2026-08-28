"""Harness for the data layer's own tests — STREAM C.

SQLite in memory, for the same reason `tests/unit/orchestrator/conftest.py` uses it:
`app/models/` declares `JSON_TYPE` with a SQLite variant, so the policy tables are exercisable
without a server and the determinism of ingestion is testable on any checkout.

What SQLite cannot prove is foreign-key enforcement, which it does not apply unless asked. The
`incident_id` and `policy_pack_id` references, and therefore the real integrity of a persisted
decision, are covered against Postgres in `tests/contract/test_policy_ingestion.py`.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 - registers every table on Base.metadata
from app.config import REPO_ROOT
from app.db.base import Base

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

#: The committed pack, read from the repository rather than a copy, so these tests fail if the
#: real pack stops satisfying them.
PACKS_ROOT = Path(REPO_ROOT) / "policy_packs"
CHARTER_ID = "in-moca-charter-2019"
CHARTER_VERSION = "2019.02"


@pytest.fixture
async def session() -> AsyncIterator:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as db:
        yield db
    await engine.dispose()


@pytest.fixture
def charter():
    """The charter pack as Stream B's loader returns it."""
    from app.config import PolicyMode
    from app.policy.loader import load_pack

    return load_pack(
        pack_dir=PACKS_ROOT,
        pack_id=CHARTER_ID,
        version=CHARTER_VERSION,
        mode=PolicyMode.charter,
    )


# --------------------------------------------------------------------------- real Postgres
# The unique constraints that make re-ingestion an update, and the foreign keys that stop a
# decision row pointing at a missing incident or pack, are the two properties SQLite cannot
# demonstrate. `TRAVELOPS_TEST_DATABASE_URL` opts in; without it these skip, exactly as the
# existing real-database tests do.


@pytest.fixture
async def pg_sessionmaker() -> AsyncIterator:
    from sqlalchemy.ext.asyncio import async_sessionmaker as pg_sessionmaker_factory

    from tests.contract.postgres_support import create_postgres_engine

    engine = create_postgres_engine()
    yield pg_sessionmaker_factory(bind=engine, expire_on_commit=False, autoflush=False)
    await engine.dispose()


@pytest.fixture
async def pg_policy_tables(pg_sessionmaker) -> AsyncIterator[None]:
    """Own the policy rows around each test.

    `tests/contract/conftest.py::clear_workflow` predates these tables and does not touch them,
    so an ingest would otherwise leak rows into whatever test ran next.
    """
    from sqlalchemy import delete

    from app.models.policy import (
        EntitlementEvaluation,
        PolicyApplicability,
        PolicyClause,
        PolicyPack,
        PolicyRule,
        PolicySourceDocument,
    )

    async def clear() -> None:
        async with pg_sessionmaker() as session:
            for model in (
                EntitlementEvaluation,
                PolicyApplicability,
                PolicyClause,
                PolicySourceDocument,
                PolicyRule,
                PolicyPack,
            ):
                await session.execute(delete(model))
            await session.commit()

    await clear()
    yield
    await clear()


#: Rows the incident fixture owns. Named so setup and teardown purge exactly the same set.
PG_INCIDENT_REFERENCE = "INC-POLICY-INGEST-01"
PG_FLIGHT_SOURCE_REF = "test:policy_ingest:flight"
PG_AIRPORT_ICAO = "ZZPI"


async def _purge_incident_fixture(session) -> None:
    """Remove the incident fixture's rows, child-first.

    Child-first and not by fixture ordering, deliberately. A test attaches
    `policy_applicability` and `entitlement_evaluation` rows to this incident, and pytest finalises
    fixtures in reverse setup order — so deleting the incident first raises a foreign-key violation,
    the airport survives the failed teardown, and the *next* test fails on a duplicate key with an
    error that says nothing about the real cause. Purging the same set at setup as well means a
    previously crashed run cannot poison the next one either.
    """
    from sqlalchemy import delete, select

    from app.models.policy import EntitlementEvaluation, PolicyApplicability
    from app.models.reference import Airport, Flight
    from app.models.workflow import Incident

    incident_ids = (
        (
            await session.execute(
                select(Incident.id).where(Incident.reference == PG_INCIDENT_REFERENCE)
            )
        )
        .scalars()
        .all()
    )
    if incident_ids:
        await session.execute(
            delete(EntitlementEvaluation).where(EntitlementEvaluation.incident_id.in_(incident_ids))
        )
        await session.execute(
            delete(PolicyApplicability).where(PolicyApplicability.incident_id.in_(incident_ids))
        )
        await session.execute(delete(Incident).where(Incident.id.in_(incident_ids)))
    await session.execute(delete(Flight).where(Flight.source_ref == PG_FLIGHT_SOURCE_REF))
    await session.execute(delete(Airport).where(Airport.icao_code == PG_AIRPORT_ICAO))
    await session.commit()


@pytest.fixture
async def pg_incident(pg_sessionmaker) -> AsyncIterator[int]:
    """A real incident on a real flight, so the applicability foreign key is exercised.

    Built directly rather than by seeding the committed dataset: this needs one flight, and
    re-seeding would reset demo-owned rows another test may be relying on.
    """
    from app.models.enums import IncidentState, ProvenanceKind, TriggerType
    from app.models.reference import Airport, Flight
    from app.models.workflow import Incident

    async with pg_sessionmaker() as session:
        await _purge_incident_fixture(session)
        session.add(
            Airport(
                icao_code=PG_AIRPORT_ICAO,
                iata_code="ZPI",
                name="Policy Ingest Test Field",
                city="Testville",
                country="IN",
                latitude=0.0,
                longitude=0.0,
                source_ref="test:policy_ingest",
            )
        )
        await session.flush()
        flight = Flight(
            flight_number="ZZ 0001",
            airline_code="ZZ",
            origin_icao=PG_AIRPORT_ICAO,
            destination_icao=PG_AIRPORT_ICAO,
            scheduled_departure=datetime(2026, 8, 20, 15, 40, tzinfo=UTC),
            scheduled_arrival=datetime(2026, 8, 20, 18, 25, tzinfo=UTC),
            block_time_minutes=165,
            status="scheduled",
            is_domestic=True,
            provenance_kind=ProvenanceKind.fixture,
            source_ref=PG_FLIGHT_SOURCE_REF,
        )
        session.add(flight)
        await session.flush()
        incident = Incident(
            reference=PG_INCIDENT_REFERENCE,
            flight_id=flight.id,
            trigger_type=TriggerType.weather,
            severity="high",
            state=IncidentState.detected,
        )
        session.add(incident)
        await session.commit()
        incident_id = incident.id

    yield incident_id

    async with pg_sessionmaker() as session:
        await _purge_incident_fixture(session)
