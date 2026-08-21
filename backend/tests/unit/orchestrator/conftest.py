"""Test harness for the orchestrator.

SQLite rather than Postgres, deliberately: `app/models/` declares `JSON_TYPE` with a
SQLite variant and the active-incident index with `sqlite_where`, so the schema — including
the partial unique index that deduplication depends on — is exercisable without a server.

Nothing here touches a Stream B or Stream C source file. The gate is stubbed by
monkeypatching the attribute at runtime, so `app/assurance/gate.py` stays untouched.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 - registers every table on Base.metadata
from app.assurance.contract import CHECK_ORDER, AssuranceResult, CheckResult, ReasonCode
from app.config import (
    LLMMode,
    NotificationMode,
    PolicyMode,
    ResolvedModes,
    Settings,
    WeatherMode,
)
from app.db.base import Base
from app.models.enums import (
    AssuranceDecision,
    CheckState,
    ProvenanceKind,
    RiskTier,
)
from app.models.reference import Airport, Flight

FIXED_NOW = datetime(2026, 8, 20, 15, 36, tzinfo=UTC)


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
def settings() -> Settings:
    """LLM_MODE=off is the mode this slice must work in."""
    return Settings(
        app_env="test",
        llm_mode=LLMMode.off,
        weather_mode=WeatherMode.fixture,
        policy_mode=PolicyMode.charter,
        notification_mode=NotificationMode.console,
        demo_dataset_id="bengaluru_storm",
    )


def make_modes(*, assurance_present: bool = True) -> ResolvedModes:
    """Resolved modes built directly, so a test can choose the safety posture it needs."""
    return ResolvedModes(
        llm=LLMMode.off,
        weather=WeatherMode.fixture,
        notification=NotificationMode.console,
        policy=PolicyMode.charter,
        real_email_enabled=False,
        assurance_config_present=assurance_present,
        assurance_config_version="assurance-v1" if assurance_present else None,
        assurance_config_hash="9f2c4b71d3e85a06" if assurance_present else None,
        degradations=[],
    )


@pytest.fixture
def modes() -> ResolvedModes:
    return make_modes()


@pytest.fixture
async def flight(session) -> Flight:
    """One VOBL departure, mirroring the bengaluru_storm fixture."""
    session.add_all(
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
    row = Flight(
        flight_number="6E 2134",
        airline_code="6E",
        origin_icao="VOBL",
        destination_icao="VIDP",
        scheduled_departure=FIXED_NOW + timedelta(minutes=4),
        scheduled_arrival=FIXED_NOW + timedelta(minutes=169),
        block_time_minutes=165,
        status="scheduled",
        is_domestic=True,
        provenance_kind=ProvenanceKind.fixture,
        source_ref="fixture:bengaluru_storm:flight",
    )
    session.add(row)
    await session.commit()
    return row


def passing_result(
    *, decision: AssuranceDecision = AssuranceDecision.execute, tier: RiskTier = RiskTier.low
) -> AssuranceResult:
    """A gate result with all six checks passing. Only a stub builds this."""
    return AssuranceResult(
        decision=decision,
        risk_tier=tier,
        checks=[
            CheckResult(name=name, state=CheckState.passed, reason_code=ReasonCode.OK)
            for name in CHECK_ORDER
        ],
        blocking=[],
        evidence_refs=["fixture:bengaluru_storm:weather:VOBL"],
        config_version="assurance-v1",
        config_hash="9f2c4b71d3e85a06",
    )


def needs_human_result(*, check=CHECK_ORDER[-1]) -> AssuranceResult:
    return AssuranceResult(
        decision=AssuranceDecision.needs_human,
        risk_tier=RiskTier.high,
        checks=[
            CheckResult(
                name=name,
                state=CheckState.failed if name == check else CheckState.passed,
                reason_code=(
                    ReasonCode.HUMAN_APPROVAL_REQUIRED if name == check else ReasonCode.OK
                ),
            )
            for name in CHECK_ORDER
        ],
        blocking=[check],
        config_version="assurance-v1",
        config_hash="9f2c4b71d3e85a06",
    )


@pytest.fixture
def no_gate(monkeypatch):
    """Remove the gate entry point.

    Stream B has landed `evaluate`, so absence is no longer the default and the fail-closed
    path has to be provoked deliberately. That is the point: the guarantee must be asserted,
    not inherited from the gate happening to be missing.
    """
    from app.assurance import gate

    monkeypatch.delattr(gate, "evaluate", raising=False)
    return gate


@pytest.fixture
def stub_gate(monkeypatch):
    """Install a fake `gate.evaluate` without editing Stream B's module.

    Returns a callable so a test can choose the decision, and records every call so a test
    can assert the orchestrator asked the gate rather than deciding for itself.
    """
    from app.assurance import gate

    calls: list[dict] = []

    def install(result_for=None):
        def evaluate(**kwargs):
            calls.append(kwargs)
            if callable(result_for):
                return result_for(**kwargs)
            return result_for if result_for is not None else passing_result()

        monkeypatch.setattr(gate, "evaluate", evaluate, raising=False)
        # `load_config_with_digest` is left alone so the committed config/assurance.v1.yaml
        # is loaded for real. Stubbing it to None would push every test down the refusal
        # path instead of the decision actually under test.
        return calls

    install.calls = calls  # type: ignore[attr-defined]
    return install
