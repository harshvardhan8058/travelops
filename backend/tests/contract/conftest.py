"""Shared fixtures for running the real application against real Postgres.

A conftest rather than an importable module, because pytest fixtures requested as test parameters
shadow any imported name of the same name — which reads as a redefinition to every linter. This is
the idiomatic place for them and needs no per-test suppressions.

Extracted so more than one contract test can drive the real app without a second copy of the
harness. Two copies would drift, and the harness is what decides whether "nothing is stubbed" is
actually true, so a drifted copy would quietly weaken the strongest verification in the suite.

Every fixture here is lazy: a contract test that does not ask for `client` never touches Postgres,
so putting them in scope for the whole directory costs nothing.

Migrations are expected to have been applied already (`alembic upgrade head`). These fixtures own
**rows, not schema**, so a test can never mask a missing migration by creating tables itself.

Repository-root importability, which this file used to provide by inserting `app.config.REPO_ROOT`
into `sys.path`, is now `pythonpath = ["..", "."]` in `backend/pyproject.toml`. The declarative form
is order-independent; the `sys.path` version depended on this conftest being imported before
anything that needed `data.generators`, and adding the fixtures below broke that assumption
immediately by moving the first `app.db.seed` import earlier.

Owner: Stream C (harness), used by Stream A's group journey test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.seed import reset_demo_dataset, seed_demo_dataset
from app.orchestrator import dispatch
from tests.contract.postgres_support import create_postgres_engine


@pytest.fixture
async def engine() -> AsyncIterator:
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

    Order matters and is explicit rather than relying on cascades: Postgres enforces the foreign
    keys and SQLite does not unless asked, so a wrong order here would pass locally and fail on the
    demo machine. The Phase 2 tables come first because they reference actions and plans.
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

    # `incident.prediction_id` points at a prediction, so the reference has to be released before
    # the predictions can go. Nulling rather than deleting incidents here: `reset_demo_dataset` owns
    # incident removal, and doing it in two places would make the order impossible to reason about.
    await session.execute(update(Incident).values(prediction_id=None))

    for model in (
        HotelInventoryHold,
        DisruptionEdge,
        CascadeSnapshot,
        PassengerImpact,
        PlanApprovalTier,
        HotelReservation,
        Notification,
        Action,
        PlanApproval,
        HumanDecision,
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

    `SERVICE_REGISTRY` is process-global, so it is populated explicitly here rather than relying on
    the app lifespan having run first.
    """
    from app.orchestrator.service_registry import register_stage2_services

    dispatch.SERVICE_REGISTRY.clear()
    actions = register_stage2_services()
    yield actions
    dispatch.SERVICE_REGISTRY.clear()


@pytest.fixture
def client(sessionmaker_for, seeded, registered) -> AsyncIterator[TestClient]:
    """The real app, pointed at the test database.

    Only `get_session` is overridden. Everything else — routers, response models, the orchestrator,
    the gate, the service registry — is the real thing, which is the whole point of this harness.
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
