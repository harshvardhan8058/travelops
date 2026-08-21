"""Real Postgres for the tests that must not run on a stand-in.

Most contract tests run on SQLite, which is fast and close enough. Three classes of bug have
already slipped through it: an unenforced foreign key, a naive-versus-aware timestamp, and a
`VARCHAR(12)` overflow. So the tests that prove the demo path — migrations, the seeded
dataset, and the workflow running through the real app — are pointed at the real engine.

`TRAVELOPS_TEST_DATABASE_URL` opts in. Without it these tests skip rather than fail, so a
checkout with no database still gets a green suite; CI and the demo machine set it.

Owner: Stream C.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

ENV_VAR = "TRAVELOPS_TEST_DATABASE_URL"

SKIP_REASON = (
    f"set {ENV_VAR} to a Postgres URL to run the real-database tests "
    "(e.g. postgresql+asyncpg://travelops:travelops@127.0.0.1:5432/travelops)"
)


def postgres_url() -> str | None:
    url = os.environ.get(ENV_VAR, "").strip()
    return url or None


requires_postgres = pytest.mark.skipif(postgres_url() is None, reason=SKIP_REASON)


def create_postgres_engine() -> AsyncEngine:
    url = postgres_url()
    if url is None:  # pragma: no cover - guarded by the marker
        raise RuntimeError(SKIP_REASON)
    # NullPool: each test gets its own connections and nothing is held between them, which
    # keeps a failed test from poisoning the next one's transaction state.
    from sqlalchemy.pool import NullPool

    return create_async_engine(url, poolclass=NullPool)
