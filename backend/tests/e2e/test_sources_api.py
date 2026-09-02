"""`GET /sources` must describe the process it is running in, not a document somebody wrote.

The ledger this replaced was a committed JSON file. It named `groq` as the reasoning provider
while `LLMProvider.openrouter` was the configured default, published `current_mode: off`
regardless of `LLM_MODE`, and carried `kind: real` on rows whose own `current_mode` said `fixture`
and on a source that had never been called. Each of those is a claim about the running system, and
none of them was read from it — which is precisely why the console's top bar and its provenance
screen could contradict each other while both were "correct".

These tests pin the properties that make that class of defect impossible to reintroduce:

* the reasoning row names the provider `provider_transport` resolves, so the ledger and the LLM
  client can never disagree about which endpoint `live` would call;
* the reasoning transport that is registered and *not* selected appears, and says so, because its
  absence is what let a reader assume the named one was the only one;
* nothing is ever both `kind: real` and presented as a live read while its mode says otherwise —
  `kind` and `usage` are separate answers to separate questions;
* `used` is only ever claimed with recorded evidence behind it. Configuration alone never earns it.

Owner: Stream A.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models
from app.config import get_settings, provider_transport
from app.db.base import Base
from app.db.seed import seed_demo_dataset
from app.db.session import get_session
from app.main import app

PREFIX = "/api/v1"


@pytest.fixture
async def ledger_engine() -> AsyncIterator:
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
async def ledger_sessions(ledger_engine):
    factory = async_sessionmaker(bind=ledger_engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        await seed_demo_dataset(session)
        await session.commit()
    return factory


@pytest.fixture
def ledger_client(ledger_sessions) -> AsyncIterator[TestClient]:
    async def override() -> AsyncIterator:
        async with ledger_sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = override
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client
    app.dependency_overrides.pop(get_session, None)


def _rows(client: TestClient) -> list[dict]:
    response = client.get(f"{PREFIX}/sources")
    assert response.status_code == 200, response.text
    return response.json()["sources"]


class TestTheLedgerDescribesThisProcess:
    def test_the_reasoning_row_names_the_provider_the_transport_resolves(self, ledger_client):
        """The one defect that started all of this: a ledger naming a provider nothing selects."""
        transport = provider_transport(get_settings())
        reasoning = next(
            row for row in _rows(ledger_client) if row["role"].startswith("Planner, explainer")
        )
        assert reasoning["provider"] == transport.provider.value
        assert reasoning["model"] == transport.model

    def test_the_unselected_transport_is_published_and_says_it_is_unused(self, ledger_client):
        """Naming it is cheaper than expecting a reader to infer it from an absence."""
        transport = provider_transport(get_settings())
        alternatives = [
            row for row in _rows(ledger_client) if row["current_mode"] == "not_selected"
        ]
        assert len(alternatives) == 1, "exactly one reasoning transport is registered but unused"
        alternative = alternatives[0]
        assert alternative["provider"] != transport.provider.value
        assert alternative["usage"] == "unused"
        assert transport.provider.value in alternative["usage_detail"]

    def test_no_row_claims_a_live_read_it_cannot_evidence(self, ledger_client):
        """`used` requires a recorded artefact. A key in the environment is not one."""
        for row in _rows(ledger_client):
            if row["usage"] == "used":
                assert row["evidence"], f"{row['name']} claims a read with nothing behind it"

    def test_kind_and_usage_are_answered_separately(self, ledger_client):
        """The old ledger folded both into one column, which is how `real` landed on a fixture row.

        In the suite's own configuration nothing external is contacted, so no row may be both a
        real source and a live read. `kind: real` on a committed snapshot is fine and honest — what
        must not happen is that row also reporting a live current mode.
        """
        for row in _rows(ledger_client):
            if row["kind"] == "real" and row["current_mode"] == "live":
                pytest.fail(f"{row['name']} reports a live read in a test run that contacts nobody")

    def test_every_row_carries_a_reason_a_reader_can_act_on(self, ledger_client):
        for row in _rows(ledger_client):
            assert row["usage_detail"].strip(), f"{row['name']} states no reason for its usage"
            assert row["role"].strip(), f"{row['name']} does not say what it is for"

    def test_the_counts_agree_with_the_rows(self, ledger_client):
        """The header must not be able to drift from the table under it."""
        payload = ledger_client.get(f"{PREFIX}/sources").json()
        rows = payload["sources"]
        assert payload["live_count"] == sum(
            1 for row in rows if row["kind"] == "real" and row["usage"] == "used"
        )
        assert payload["unused_count"] == sum(1 for row in rows if row["usage"] == "unused")
        assert payload["unavailable_count"] == sum(
            1 for row in rows if row["usage"] == "unavailable"
        )


class TestTheEndpointIsRealRatherThanAFile:
    def test_it_declares_a_response_model(self, ledger_client):
        """A fixture route returned `Any`, which is how its schema rendered as `"string"`."""
        spec = ledger_client.get("/openapi.json").json()
        schema = spec["paths"][f"{PREFIX}/sources"]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        assert schema["$ref"].endswith("SourcesResponse")

    def test_the_summary_no_longer_advertises_a_fixture(self, ledger_client):
        spec = ledger_client.get("/openapi.json").json()
        assert "[fixture]" not in spec["paths"][f"{PREFIX}/sources"]["get"]["summary"]
