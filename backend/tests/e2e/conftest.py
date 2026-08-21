"""End-to-end harness: the real FastAPI app over a real (SQLite) database.

The app's `get_session` dependency is overridden rather than the engine being called
directly, so these tests exercise routing, response models, the error envelope and the
session lifecycle — the parts a unit test cannot reach.

Owner: Stream A.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models
from app.db.base import Base
from app.db.session import get_session
from app.main import app
from app.models.enums import ProvenanceKind
from app.models.reference import Airport, Flight, WeatherObservation

DEPARTURE = datetime(2026, 8, 20, 15, 40, tzinfo=UTC)
OBSERVED_AT = datetime(2026, 8, 20, 15, 30, tzinfo=UTC)


@pytest.fixture
async def db_engine() -> AsyncIterator:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def seeded(db_engine) -> AsyncIterator:
    """The bengaluru_storm shape: one VOBL departure and its observation."""
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False, autoflush=False)
    async with factory() as db:
        db.add_all(
            [
                Airport(
                    icao_code="VOBL",
                    iata_code="BLR",
                    name="Kempegowda International",
                    city="Bengaluru",
                    country="IN",
                    latitude=13.198889,
                    longitude=77.705556,
                    source_ref="fixture:bengaluru_storm",
                ),
                Airport(
                    icao_code="VIDP",
                    iata_code="DEL",
                    name="Indira Gandhi International",
                    city="Delhi",
                    country="IN",
                    latitude=28.5665,
                    longitude=77.103088,
                    source_ref="fixture:bengaluru_storm",
                ),
            ]
        )
        db.add(
            WeatherObservation(
                airport_icao="VOBL",
                observed_at=OBSERVED_AT,
                wind_speed_kt=24,
                wind_direction_deg=250,
                visibility_m=800,
                ceiling_ft=900,
                precipitation="rain",
                provenance_kind=ProvenanceKind.fixture,
                provenance_provider="fixture",
                source_ref="fixture:bengaluru_storm:weather:VOBL",
            )
        )
        db.add(
            Flight(
                flight_number="6E 2134",
                airline_code="6E",
                origin_icao="VOBL",
                destination_icao="VIDP",
                scheduled_departure=DEPARTURE,
                scheduled_arrival=DEPARTURE + timedelta(minutes=165),
                estimated_departure=DEPARTURE + timedelta(minutes=420),
                block_time_minutes=165,
                status="delayed",
                is_domestic=True,
                provenance_kind=ProvenanceKind.fixture,
                source_ref="fixture:bengaluru_storm:flight",
            )
        )
        await db.commit()
    yield db_engine


@pytest.fixture(autouse=True)
def registered_services() -> AsyncIterator[list[str]]:
    """Bind the implemented services, as the application lifespan does in production.

    Made explicit rather than left to whichever test happened to construct a TestClient
    first: `dispatch.SERVICE_REGISTRY` is process-global, so relying on lifespan ordering
    makes these tests depend on each other.
    """
    from app.orchestrator import dispatch
    from app.orchestrator.service_registry import register_stage2_services

    dispatch.SERVICE_REGISTRY.clear()
    yield register_stage2_services()
    dispatch.SERVICE_REGISTRY.clear()


@pytest.fixture
def client(seeded) -> AsyncIterator[TestClient]:
    factory = async_sessionmaker(bind=seeded, expire_on_commit=False, autoflush=False)

    async def override() -> AsyncIterator:
        async with factory() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    app.dependency_overrides[get_session] = override
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
async def incident(seeded) -> str:
    """Open one incident through the engine, returning its reference."""
    from app.orchestrator.engine import Orchestrator

    factory = async_sessionmaker(bind=seeded, expire_on_commit=False, autoflush=False)
    async with factory() as db:
        orchestrator = Orchestrator(db)
        ctx = await orchestrator.open_incident(
            1, "weather", evidence_refs=["fixture:bengaluru_storm:weather:VOBL"]
        )
        return ctx.incident_reference
