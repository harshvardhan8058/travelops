"""Repository-root importability, plus the shared real-app-over-real-Postgres harness.

`pythonpath = ["..", "."]` in `backend/pyproject.toml` now makes `data.generators` importable
declaratively, which is order-independent. The `sys.path` insertion below is kept as belt to that
brace, and because `tests/unit/services/conftest.py` does the same: `app.config.REPO_ROOT` stays the
single definition of where the repository root is.

The fixtures are here rather than in one test module because more than one contract test drives the
real app now, and a second copy of the harness would drift. The harness is what decides whether
"nothing is stubbed" is actually true, so a drifted copy would quietly weaken the strongest
verification in the suite.

Every fixture is lazy: a contract test that never asks for `client` never touches Postgres, so
putting them in scope for the directory costs nothing.

Migrations are expected to have been applied already (`alembic upgrade head`). These fixtures own
**rows, not schema**, so a test can never mask a missing migration by creating tables itself.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import REPO_ROOT
from app.db.seed import reset_demo_dataset, seed_demo_dataset
from app.orchestrator import dispatch
from tests.contract.postgres_support import create_postgres_engine

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(scope="session")
async def engine() -> AsyncIterator:
    """One engine for the whole session.

    Per-test engines exhausted Postgres' connection limit once the group-journey tests
    arrived: each drives the full eight-incident cascade through the HTTP app, and a fresh
    engine per test on top of that tipped the run into `too many clients`. That surfaces as
    a wall of setup errors rather than a test failure, which is the least diagnosable shape
    a problem can take.

    Sharing an engine is safe because these fixtures own rows, not schema, and `seeded`
    resets them around every test. `create_postgres_engine` uses `NullPool`, so connections
    still are not held between tests.
    """
    db = create_postgres_engine()
    yield db
    await db.dispose()


@pytest.fixture
async def sessionmaker_for(engine):
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@pytest.fixture
async def seeded(sessionmaker_for) -> AsyncIterator[None]:
    """The committed dataset, seeded and torn down around each test."""
    async with sessionmaker_for() as session:
        await clear_workflow(session)
        await reset_demo_dataset(session)
        await seed_demo_dataset(session)
        await session.commit()
    yield
    async with sessionmaker_for() as session:
        await clear_workflow(session)
        await reset_demo_dataset(session)
        await session.commit()


async def clear_workflow(session) -> None:
    """Remove any workflow output left by an earlier run, child-first.

    Order is explicit rather than relying on cascades: Postgres enforces the foreign keys and SQLite
    does not unless asked, so a wrong order would pass locally and fail on the demo machine. The
    Phase 2 tables come first because they reference actions, plans and groups.

    `incident.prediction_id` is nulled before predictions are deleted. Incidents themselves are left
    to `reset_demo_dataset`, which owns them — removing them in two places would make the order
    impossible to reason about.
    """
    from sqlalchemy import delete, update

    from app.models.cascade import (
        CascadeSnapshot,
        DisruptionEdge,
        HotelInventoryHold,
        PassengerImpact,
        PlanApproval,
        PlanApprovalTier,
    )
    from app.models.workflow import (
        Action,
        AssuranceEvaluation,
        DecisionLog,
        HotelReservation,
        HumanDecision,
        Incident,
        Notification,
        Plan,
        PlanTask,
        Prediction,
    )

    await session.execute(update(Incident).values(prediction_id=None))
    for model in (
        HotelInventoryHold,
        DisruptionEdge,
        CascadeSnapshot,
        PassengerImpact,
        HotelReservation,
        Notification,
        Action,
        HumanDecision,
        PlanApprovalTier,
        PlanApproval,
        AssuranceEvaluation,
        PlanTask,
        Plan,
        DecisionLog,
        Prediction,
    ):
        await session.execute(delete(model))
    await session.flush()


@pytest.fixture
def registered() -> AsyncIterator[list[str]]:
    """Stream A's registration path, and only that one.

    `SERVICE_REGISTRY` is process-global, so it is populated explicitly rather than relying on the
    app lifespan having run first.
    """
    from app.orchestrator.service_registry import register_stage2_services

    dispatch.SERVICE_REGISTRY.clear()
    actions = register_stage2_services()
    yield actions
    dispatch.SERVICE_REGISTRY.clear()


@pytest.fixture
def client(sessionmaker_for, seeded, registered) -> AsyncIterator[TestClient]:
    """The real app, pointed at the test database.

    Only `get_session` is overridden. Routers, response models, the orchestrator, the gate and the
    service registry are all the real thing, which is the point of this harness.
    """
    from app.db.session import get_session
    from app.main import app

    async def override() -> AsyncIterator:
        async with sessionmaker_for() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = override
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_session, None)
