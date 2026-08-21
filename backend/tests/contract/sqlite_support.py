"""SQLite that behaves like the target engine.

SQLite does not enforce foreign keys unless told to, so a delete in the wrong order passes
here and raises `ForeignKeyViolationError` on Postgres. That is not hypothetical: it is exactly
how `reset_demo_dataset` came to leave `decision_log` rows pointing at deleted incidents, which
would have failed `make reset` on the demo machine and nowhere else.

Every SQLite engine in the contract tests is built through here, with `PRAGMA foreign_keys=ON`,
so the stand-in is as strict as the thing it stands in for.

Owner: Stream C.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def create_sqlite_engine(path: Path) -> AsyncEngine:
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _enforce_foreign_keys(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


async def foreign_keys_are_enforced(engine: AsyncEngine) -> bool:
    async with engine.connect() as connection:
        result = await connection.execute(text("PRAGMA foreign_keys"))
        return bool(result.scalar())
